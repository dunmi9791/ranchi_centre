# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, MissingError, RedirectWarning, UserError, ValidationError

_logger = logging.getLogger(__name__)
# Business errors the web client already knows how to show. Anything else raised while
# posting is turned into a UserError so the RPC payload keeps a data.message.
_HANDLED_ERRORS = (UserError, ValidationError, AccessError, MissingError, RedirectWarning)

MANAGER_GROUP = 'ranchi_centre.group_ranchi_manager'
GENERAL_MANAGER_GROUP = 'ranchi_centre.group_ranchi_general_manager'
# Fields that make up what was collected; frozen once the officer hands the cash over.
LOCKED_FIELDS = {'union_id', 'date', 'journal_id', 'company_id', 'credit_officer_id', 'line_ids'}


class RanchiCollection(models.Model):
    _name = 'ranchi.collection'
    _description = 'Ranchi Field Collection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string="Reference", readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True, index=True)
    union_id = fields.Many2one(
        'ranchi.union', string="Union", required=True, tracking=True, check_company=True,
        domain="[('state', '=', 'active')]")
    loan_type_id = fields.Many2one(
        related='union_id.loan_type_id', string="Loan Type", store=True, index=True,
        help="Product the union runs (e.g. Ranchi weekly or Rapid daily); decides which manager takes the cash.")
    credit_officer_id = fields.Many2one(
        'hr.employee', string="Collected By", tracking=True, check_company=True,
        compute='_compute_credit_officer', store=True, readonly=False)
    journal_id = fields.Many2one(
        'account.journal', string="Journal", required=True, check_company=True, tracking=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Cash Submitted'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    line_ids = fields.One2many('ranchi.collection.line', 'collection_id', string="Lines", copy=True)
    line_count = fields.Integer(compute='_compute_totals')
    amount_loan_total = fields.Monetary(compute='_compute_totals', store=True, string="Loan Repayments")
    amount_savings_total = fields.Monetary(compute='_compute_totals', store=True, string="Savings Deposits")
    amount_fees_total = fields.Monetary(
        compute='_compute_totals', store=True, string="Fees",
        help="Registration fees and fees before disbursement collected from the members.")
    amount_total = fields.Monetary(compute='_compute_totals', store=True, string="Total Collected")
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    note = fields.Text()
    origin = fields.Selection(
        [('sheet', 'Collection sheet'), ('registration', 'Member registration')],
        default='sheet', required=True, readonly=True, copy=False,
        help="Registration: fees and opening savings the officer took while enrolling a new member.")
    # cash handover: the officer submits, a manager counts the cash and confirms before posting
    submitted_by_id = fields.Many2one('res.users', string="Submitted By", readonly=True, copy=False)
    submitted_date = fields.Datetime(string="Submitted On", readonly=True, copy=False)
    amount_received = fields.Monetary(
        string="Cash Received", copy=False, tracking=True,
        help="Cash counted by the manager at handover. It must match the total collected before posting.")
    received_by_id = fields.Many2one('res.users', string="Received By", readonly=True, copy=False, tracking=True)
    received_date = fields.Datetime(string="Received On", readonly=True, copy=False)
    return_reason = fields.Char(string="Returned Because", copy=False, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.collection') or '/'
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            if vals.get('state') == 'posted' or 'amount_received' in vals:
                raise AccessError(_("Only a manager can confirm the cash and post a collection."))
            if LOCKED_FIELDS & set(vals) and any(col.state != 'draft' for col in self):
                raise UserError(_("This collection has been handed over; ask a manager to return it before editing."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(col.state in ('submitted', 'posted') for col in self):
            raise UserError(_("Submitted or posted collections cannot be deleted."))

    @api.depends('union_id')
    def _compute_credit_officer(self):
        for col in self:
            if col.union_id and not col.credit_officer_id:
                col.credit_officer_id = col.union_id.credit_officer_id

    @api.depends('line_ids.amount_loan', 'line_ids.amount_savings', 'line_ids.amount_fees')
    def _compute_totals(self):
        for col in self:
            col.line_count = len(col.line_ids)
            col.amount_loan_total = sum(col.line_ids.mapped('amount_loan'))
            col.amount_savings_total = sum(col.line_ids.mapped('amount_savings'))
            col.amount_fees_total = sum(col.line_ids.mapped('amount_fees'))
            col.amount_total = col.amount_loan_total + col.amount_savings_total + col.amount_fees_total

    def action_fill_due_installments(self):
        """Add one line per member of the union with an installment or a fee due on or before the
        collection date."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("Only draft collections can be pre-filled."))
        existing = self.line_ids.mapped('loan_id')
        loans = self.env['ranchi.loan'].search([
            ('union_id', '=', self.union_id.id), ('state', '=', 'disbursed'), ('id', 'not in', existing.ids)])
        lines = []
        for loan in loans:
            due = loan.installment_ids.filtered(
                lambda i: i.state != 'paid' and i.date_due and i.date_due <= self.date)
            if not due:
                continue
            lines.append((0, 0, {
                'member_id': loan.member_id.id,
                'loan_id': loan.id,
                'amount_loan': sum(due.mapped('amount_residual')),
            }))
        with_lines = self.line_ids.member_id | self.env['res.partner'].browse([l[2]['member_id'] for l in lines])
        members = self.env['res.partner'].search([
            ('is_ranchi_member', '=', True), ('union_id', '=', self.union_id.id),
            ('membership_state', '!=', 'exited'), ('id', 'not in', with_lines.ids)])
        for member in members:
            fees = member._fees_due_total() - member._pending_collected('amount_fees')
            if not self.currency_id.is_zero(fees) and fees > 0:
                lines.append((0, 0, {'member_id': member.id, 'amount_fees': fees}))
        if lines:
            self.write({'line_ids': lines})
        return True

    def _check_manager(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a manager can confirm the cash and post a collection."))
        if user.has_group(GENERAL_MANAGER_GROUP):
            return
        # each product (Ranchi weekly, Rapid daily) has its own field managers
        for col in self:
            if user not in col.sudo().loan_type_id.manager_ids:
                raise AccessError(_(
                    "%(col)s belongs to %(type)s; only that product's managers can handle its cash.",
                    col=col.name, type=col.sudo().loan_type_id.name))

    def action_submit(self):
        """Credit officer hands the collection and its cash over to the manager."""
        for col in self:
            if col.state != 'draft':
                raise UserError(_("Only draft collections can be submitted."))
            if not col.line_ids:
                raise UserError(_("Add at least one line before submitting."))
            for line in col.line_ids:
                line._validate()
        self.write({
            'state': 'submitted',
            'submitted_by_id': self.env.uid,
            'submitted_date': fields.Datetime.now(),
            'return_reason': False,
        })
        for col in self:
            col.message_post(body=_("Submitted for cash handover: %s expected.",
                                    col.currency_id.format(col.amount_total)))
        return True

    def action_return_to_officer(self):
        """Manager sends the collection back, e.g. when the cash does not match the sheet."""
        self._check_manager()
        for col in self:
            if col.state != 'submitted':
                raise UserError(_("Only submitted collections can be returned."))
        self.sudo().write({'state': 'draft', 'amount_received': 0.0})
        for col in self:
            col.message_post(body=_("Returned to the credit officer: %s", col.return_reason or _("no reason given")))
        return True

    def action_confirm_cash(self):
        """Manager confirms the cash counted matches the collection, then posts it.

        Safe to run again. A collection that already has its journal entry is not
        booked a second time, so a retry after a lost response does not duplicate
        repayments or deposits.
        """
        self._check_manager()
        todo = self.browse()
        for col in self:
            if col.state == 'posted' and col.move_id:
                continue
            if col.state != 'submitted':
                raise UserError(_("The credit officer must submit the collection before the cash is confirmed."))
            if col.currency_id.compare_amounts(col.amount_received, col.amount_total) != 0:
                raise UserError(_(
                    "Cash received (%(received)s) does not match the total collected (%(total)s) on %(ref)s. "
                    "Correct the count or return the collection to the officer.",
                    received=col.currency_id.format(col.amount_received),
                    total=col.currency_id.format(col.amount_total), ref=col.name))
            todo |= col
        if not todo:
            return True
        todo.write({'received_by_id': self.env.uid, 'received_date': fields.Datetime.now()})
        return todo.action_post()

    def action_post(self):
        self._check_manager()
        for col in self:
            try:
                # The journal entry needs accounting rights the posting user may not have, so the
                # posting runs as superuser once the manager check above has passed.
                col.sudo()._post()
            except _HANDLED_ERRORS:
                raise
            except Exception as err:
                # A database error has already aborted the cursor; let Odoo serialise it.
                if type(err).__module__.split('.')[0] in ('psycopg2', 'psycopg'):
                    raise
                _logger.exception("Failed to post collection %s", col.display_name)
                raise UserError(_(
                    "Could not post %(ref)s: %(error)s",
                    ref=col.display_name, error=err,
                )) from err
        return True

    def _lock_for_posting(self):
        """Block a second confirmation of this row until the first one finishes."""
        self.ensure_one()
        if not self._table.isidentifier():
            raise UserError(_("Cannot lock collection %s for posting.", self.display_name))
        self.env.cr.execute(
            'SELECT id FROM "%s" WHERE id = %%s FOR UPDATE' % self._table,
            (self.id,),
        )
        self.invalidate_recordset()

    def _post(self):
        self.ensure_one()
        self._lock_for_posting()
        if self.state == 'posted' and self.move_id:
            return
        if self.state == 'posted':
            raise UserError(_(
                "%s is marked posted but has no journal entry. It was not posted again.", self.name))
        if self.state != 'submitted':
            raise UserError(_("Only collections whose cash has been submitted can be posted."))
        if not self.line_ids:
            raise UserError(_("Add at least one line before posting."))
        if not self.received_by_id:
            raise UserError(_("Confirm the cash received before posting %s.", self.name))
        move = self.move_id
        if move and move.state == 'cancel':
            move = self.env['account.move']
        allocations_by_line = {}
        if move:
            # A previous attempt already created the entry. Finish the sub-ledger
            # links if they are missing; never create a second entry.
            self._refuse_partial_move(move)
            self._refuse_unlinked_fees()
            if move.state == 'draft':
                move.action_post()
        else:
            for line in self.line_ids:
                line._validate()
            move, allocations_by_line = self._create_collection_move()
        self._ensure_subledgers(move, allocations_by_line)
        self.write({'state': 'posted', 'move_id': move.id})
        self.message_post(body=_("Collection posted. Journal entry %s.", move.name))

    def _booked_cash(self, move):
        account = self.journal_id.default_account_id
        return sum(move.line_ids.filtered(lambda line: line.account_id == account).mapped('debit'))

    def _refuse_partial_move(self, move):
        """A journal entry that does not cover the whole sheet must not be topped up."""
        booked = self._booked_cash(move)
        if self.currency_id.compare_amounts(booked, self.amount_total) == 0:
            return
        raise UserError(_(
            "%(ref)s is only partly posted: journal entry %(move)s books %(booked)s of the "
            "%(total)s collected. It was left untouched so repayments and deposits are not "
            "booked a second time. Reverse that entry, or ask an accountant to finish it, "
            "before confirming this collection again.",
            ref=self.name, move=move.name or move.ref or move.id,
            booked=self.currency_id.format(booked), total=self.currency_id.format(self.amount_total)))

    def _refuse_unlinked_fees(self):
        for line in self.line_ids:
            if not self.currency_id.is_zero(line.amount_fees) and not line.fee_move_ids:
                raise UserError(_(
                    "%(ref)s already has journal entry %(move)s but the fees for %(member)s "
                    "are not linked to it. It was not posted again.",
                    ref=self.name, move=self.move_id.display_name, member=line.member_id.display_name))

    def _create_collection_move(self):
        """Create and post the single cash entry for this sheet. Fees are reconciled here."""
        self.ensure_one()
        company = self.company_id
        currency = self.currency_id
        cash_account = self.journal_id.default_account_id
        if not cash_account:
            raise UserError(_("Journal %s has no default account.", self.journal_id.name))
        liability = None
        if not currency.is_zero(self.amount_savings_total):
            liability = company._ranchi_require('ranchi_savings_liability_account_id')
        fee_settlements = {}  # credit line label -> the invoice's open receivable lines
        allocations_by_line = {}
        lines = [(0, 0, {
            'name': _("Collection %(ref)s - %(union)s", ref=self.name, union=self.union_id.name),
            'account_id': cash_account.id,
            'debit': self.amount_total,
            'credit': 0.0,
        })]
        for line in self.line_ids:
            if not currency.is_zero(line.amount_loan):
                allocations = line.loan_id._allocate_repayment(line.amount_loan)
                allocations_by_line[line] = allocations
                lines += line.loan_id._prepare_repayment_credit_lines(
                    allocations, label=_("%(ref)s repayment %(loan)s", ref=self.name, loan=line.loan_id.name))
            if not currency.is_zero(line.amount_savings):
                lines.append((0, 0, {
                    'name': _("%(ref)s savings deposit %(member)s", ref=self.name, member=line.member_id.name),
                    'account_id': liability.id,
                    'partner_id': line.member_id.id,
                    'debit': 0.0,
                    'credit': line.amount_savings,
                }))
            if not currency.is_zero(line.amount_fees):
                remaining = line.amount_fees
                invoices = line.member_id._fee_invoices_to_settle()
                for invoice in invoices:
                    if currency.compare_amounts(remaining, 0.0) <= 0:
                        break
                    portion = min(remaining, invoice.amount_residual)
                    remaining = currency.round(remaining - portion)
                    receivable = invoice.line_ids.filtered(
                        lambda l: l.display_type == 'payment_term' and not l.reconciled)
                    if not receivable:
                        raise UserError(_(
                            "Invoice %(invoice)s for %(member)s has no open receivable to settle.",
                            invoice=invoice.display_name, member=line.member_id.display_name))
                    # The line id keeps the label unique when two members share an invoice name.
                    label = _("%(ref)s fees %(invoice)s line %(line)s",
                              ref=self.name, invoice=invoice.name, line=line.id)
                    lines.append((0, 0, {
                        'name': label,
                        'account_id': receivable[:1].account_id.id,
                        'partner_id': line.member_id.id,
                        'debit': 0.0,
                        'credit': portion,
                    }))
                    fee_settlements[label] = receivable
                    line.fee_move_ids = [(4, invoice.id)]
                if currency.compare_amounts(remaining, 0.0) > 0:
                    raise UserError(_(
                        "%(member)s paid %(paid)s of fees on %(ref)s but only %(due)s is due.",
                        member=line.member_id.display_name, paid=currency.format(line.amount_fees),
                        ref=self.name, due=currency.format(line.amount_fees - remaining)))
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'company_id': company.id,
            'date': self.date,
            'ref': _("Field collection %s", self.name),
            'ranchi_move_type': 'collection',
            'line_ids': lines,
        })
        move.action_post()
        # Reconciling settles the fee invoices; once one is fully paid its hook moves the loan to
        # 'Fees Paid' or vets the new member.
        for label, receivable in fee_settlements.items():
            credit = move.line_ids.filtered(lambda l: l.name == label and l.credit)
            (credit | receivable).reconcile()
        return move, allocations_by_line

    def _ensure_subledgers(self, move, allocations_by_line=None):
        """Create missing repayment and deposit rows for an existing entry. Never a second entry.

        *allocations_by_line* is the split already written on a move we just created. Recomputing
        it after the first line is reconciled would see a lower balance and book the rest wrong.
        """
        self.ensure_one()
        currency = self.currency_id
        company = self.company_id
        allocations_by_line = allocations_by_line or {}
        liability = None
        if any(not currency.is_zero(line.amount_savings) and not line.savings_transaction_id
               for line in self.line_ids):
            liability = company._ranchi_require('ranchi_savings_liability_account_id')
        used_savings_lines = set(self.line_ids.mapped('savings_transaction_id.move_line_id').ids)
        used_credit_ids = set(self.line_ids.repayment_ids.move_line_id.ids)
        Tx = self.env['ranchi.savings.transaction']
        for line in self.line_ids:
            if not currency.is_zero(line.amount_loan):
                allocations = allocations_by_line.get(line)
                # A move just created has no repayment rows yet, so the allocations already
                # written on it can be registered in one pass. An older entry may have only
                # some of its installment credits linked; those are matched one by one.
                if allocations and not line.repayment_ids:
                    line.loan_id._register_repayments(
                        move, allocations, 'collection', collection_line_id=line.id)
                    used_credit_ids.update(line.repayment_ids.move_line_id.ids)
                else:
                    self._link_existing_repayments(line, move, used_credit_ids)
            if currency.is_zero(line.amount_savings) or line.savings_transaction_id:
                continue
            liability_line = move.line_ids.filtered(
                lambda l: l.account_id == liability and l.partner_id == line.member_id and l.credit
                and l.id not in used_savings_lines
                and currency.compare_amounts(l.credit, line.amount_savings) == 0)[:1]
            if not liability_line:
                raise UserError(_(
                    "Journal entry %(move)s has no savings line left for %(member)s (%(amount)s). "
                    "%(ref)s was not posted again.",
                    move=move.display_name, member=line.member_id.display_name,
                    amount=currency.format(line.amount_savings), ref=self.name))
            used_savings_lines.add(liability_line.id)
            tx = Tx.create({
                'member_id': line.member_id.id,
                'company_id': company.id,
                'date': self.date,
                'amount': line.amount_savings,
                'type': 'deposit',
                'origin': 'collection',
                'journal_id': self.journal_id.id,
                'collection_line_id': line.id,
                'move_id': move.id,
                'move_line_id': liability_line.id,
                'note': _("Collected on %s", self.name),
                'state': 'posted',
            })
            line.savings_transaction_id = tx

    def _reconcile_repayment_credit(self, credit):
        """Reconcile one collection credit with the installment it pays. Already reconciled is a no-op."""
        installment = credit.ranchi_installment_id
        if not installment or credit.reconciled:
            return
        receivable = installment.move_line_id
        if not receivable:
            raise UserError(_(
                "Installment %(inst)s on %(loan)s has no receivable line to reconcile with %(move)s. "
                "%(ref)s was not posted again.",
                inst=installment.display_name, loan=installment.loan_id.display_name,
                move=credit.move_id.display_name, ref=self.name))
        (receivable | credit).reconcile()

    def _link_existing_repayments(self, line, move, used_credit_ids):
        """Link each of this line's installment credits that does not already have a repayment.

        One repayment row does not mean the line is finished: a single collection line can pay
        several installments, and only some of those credits may have been recorded.
        """
        currency = self.currency_id
        loan = line.loan_id
        own = line.repayment_ids.filtered(lambda repayment: repayment.move_line_id.move_id == move)
        for repayment in own:
            credit = repayment.move_line_id
            used_credit_ids.add(credit.id)
            self._reconcile_repayment_credit(credit)
        covered = sum(own.mapped('amount'))
        credits = move.line_ids.filtered(
            lambda aml, loan=loan: aml.credit and aml.ranchi_installment_id
            and aml.ranchi_installment_id.loan_id == loan and aml.id not in used_credit_ids)
        Repayment = self.env['ranchi.loan.repayment']
        for credit in credits:
            if currency.compare_amounts(covered, line.amount_loan) >= 0:
                break
            if currency.compare_amounts(covered + credit.credit, line.amount_loan) > 0:
                continue
            Repayment.create({
                'loan_id': loan.id,
                'installment_id': credit.ranchi_installment_id.id,
                'date': move.date,
                'amount': credit.credit,
                'source': 'collection',
                'move_line_id': credit.id,
                'collection_line_id': line.id,
            })
            used_credit_ids.add(credit.id)
            covered = currency.round(covered + credit.credit)
            self._reconcile_repayment_credit(credit)
        if currency.compare_amounts(covered, line.amount_loan) != 0:
            raise UserError(_(
                "%(ref)s already has journal entry %(move)s but only %(covered)s of the %(paid)s "
                "repayment for %(member)s is linked. It was not posted again.",
                ref=self.name, move=move.display_name, member=line.member_id.display_name,
                covered=currency.format(covered), paid=currency.format(line.amount_loan)))
        loan._check_fully_paid()

    def action_cancel(self):
        for col in self:
            if col.state == 'submitted':
                raise UserError(_("Ask a manager to return %s to draft before cancelling it.", col.name))
            if col.state == 'posted':
                raise UserError(_(
                    "Posted collections cannot be cancelled here; reverse the journal entry from Accounting "
                    "and record a corrective collection."))
            col.state = 'cancelled'

    def action_reset_to_draft(self):
        for col in self:
            if col.state != 'cancelled':
                raise UserError(_("Only cancelled collections can be reset to draft."))
            col.state = 'draft'

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }


class RanchiCollectionLine(models.Model):
    _name = 'ranchi.collection.line'
    _description = 'Ranchi Field Collection Line'
    _order = 'collection_id, id'

    collection_id = fields.Many2one('ranchi.collection', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='collection_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='collection_id.currency_id')
    union_id = fields.Many2one(related='collection_id.union_id')
    date = fields.Date(related='collection_id.date', store=True)
    state = fields.Selection(related='collection_id.state', store=True)
    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, index=True,
        domain="[('is_ranchi_member', '=', True), ('union_id', '=', union_id)]")
    loan_id = fields.Many2one(
        'ranchi.loan', string="Loan", index=True,
        domain="[('member_id', '=', member_id), ('state', '=', 'disbursed')]")
    loan_balance = fields.Monetary(related='loan_id.balance')
    amount_due = fields.Monetary(compute='_compute_amount_due', string="Due Today")
    amount_loan = fields.Monetary(string="Loan Repayment", default=0.0)
    amount_savings = fields.Monetary(string="Savings Deposit", default=0.0)
    amount_fees = fields.Monetary(
        string="Fees", default=0.0,
        help="Registration fees and fees before disbursement paid by the member. They settle the "
             "registration invoice first, then the fee invoice of the approved loan.")
    fees_due = fields.Monetary(compute='_compute_fees_due', string="Fees Due")
    fee_move_ids = fields.Many2many(
        'account.move', 'ranchi_collection_line_fee_move_rel', 'line_id', 'move_id',
        string="Fee Invoices Settled", readonly=True, copy=False)
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True)
    repayment_ids = fields.One2many('ranchi.loan.repayment', 'collection_line_id')
    savings_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True)
    note = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_collection_editable()
        return lines

    def write(self, vals):
        if not self.env.su:
            self._check_collection_editable(vals)
        # The form saves every line again when the manager records Cash Received. Rewriting
        # unchanged lines re-runs their constraints (fee lookups per member) and is what makes
        # that save heavy enough for a proxy to drop it. Skip a write that changes nothing.
        changed = self.filtered(lambda line: line._vals_change(vals))
        if not changed:
            return True
        return super(RanchiCollectionLine, changed).write(vals)

    def unlink(self):
        self._check_collection_editable()
        return super().unlink()

    def _check_collection_editable(self, vals=None):
        if self.env.su:
            return
        locked = self.filtered(lambda line: line.collection_id.state != 'draft')
        if not locked:
            return
        if vals is not None and not any(line._vals_change(vals) for line in locked):
            return
        raise UserError(_("Lines can only be changed while the collection is a draft."))

    def _vals_change(self, vals):
        """True when *vals* would actually modify this line (currency rounding counts as equal)."""
        self.ensure_one()
        currency = self.currency_id
        for fname, value in vals.items():
            field = self._fields.get(fname)
            if not field:
                continue
            if field.type == 'many2one':
                new_id = value.id if isinstance(value, models.BaseModel) else value
                if (self[fname].id or False) != (new_id or False):
                    return True
            elif field.type in ('one2many', 'many2many'):
                if value:
                    return True
            elif field.type in ('float', 'monetary'):
                current = self[fname] or 0.0
                new = value or 0.0
                if currency:
                    if currency.compare_amounts(current, new) != 0:
                        return True
                elif current != new:
                    return True
            elif field.type == 'integer':
                if (self[fname] or 0) != (value or 0):
                    return True
            elif field.type == 'boolean':
                if bool(self[fname]) != bool(value):
                    return True
            elif (self[fname] or False) != (value or False):
                return True
        return False

    @api.depends('amount_loan', 'amount_savings', 'amount_fees')
    def _compute_amount_total(self):
        for line in self:
            line.amount_total = line.amount_loan + line.amount_savings + line.amount_fees

    @api.depends('member_id')
    def _compute_fees_due(self):
        for line in self:
            line.fees_due = line.member_id._fees_due_total() if line.member_id else 0.0

    @api.depends('loan_id', 'collection_id.date')
    def _compute_amount_due(self):
        for line in self:
            date = line.collection_id.date or fields.Date.context_today(self)
            due = line.loan_id.installment_ids.filtered(
                lambda i: i.state != 'paid' and i.date_due and i.date_due <= date)
            line.amount_due = sum(due.mapped('amount_residual'))

    @api.onchange('member_id')
    def _onchange_member_id(self):
        if self.member_id and self.loan_id.member_id != self.member_id:
            self.loan_id = self.member_id.active_loan_id if self.member_id.active_loan_id.state == 'disbursed' else False

    @api.onchange('member_id')
    def _onchange_member_fees(self):
        if self.member_id and not self.amount_fees and self.fees_due > 0:
            self.amount_fees = self.fees_due

    @api.onchange('loan_id')
    def _onchange_loan_id(self):
        if self.loan_id and not self.amount_loan:
            self.amount_loan = self.amount_due

    @api.constrains('amount_loan', 'amount_savings', 'amount_fees', 'loan_id', 'member_id')
    def _check_line(self):
        for line in self:
            line._validate()

    def _validate(self):
        self.ensure_one()
        currency = self.currency_id
        if self.amount_loan < 0 or self.amount_savings < 0 or self.amount_fees < 0:
            raise ValidationError(_("Amounts cannot be negative."))
        if currency and currency.is_zero(self.amount_loan) and currency.is_zero(self.amount_savings) \
                and currency.is_zero(self.amount_fees):
            raise ValidationError(_(
                "Enter a loan repayment, a savings deposit or fees for %s.", self.member_id.display_name))
        if currency and not currency.is_zero(self.amount_fees) and self.state in ('draft', 'submitted'):
            due = self.member_id._fees_due_total()
            if self.state == 'draft':
                # other collections already handed over will settle part of it
                due -= self.member_id._pending_collected('amount_fees')
            if currency.compare_amounts(self.amount_fees, due) > 0:
                raise ValidationError(_(
                    "%(member)s owes %(due)s of fees; %(paid)s was entered.",
                    member=self.member_id.display_name, due=currency.format(due),
                    paid=currency.format(self.amount_fees)))
        if self.amount_loan and not self.loan_id:
            raise ValidationError(_("Select the loan being repaid by %s.", self.member_id.display_name))
        if self.loan_id and self.loan_id.member_id != self.member_id:
            raise ValidationError(_("Loan %s does not belong to %s.", self.loan_id.name, self.member_id.display_name))
        if self.member_id.union_id and self.collection_id.union_id \
                and self.member_id.union_id != self.collection_id.union_id:
            raise ValidationError(_(
                "%(member)s belongs to %(union)s, not to this collection's union.",
                member=self.member_id.display_name, union=self.member_id.union_id.name))
        if self.loan_id and currency and currency.compare_amounts(self.amount_loan, self.loan_id.balance) > 0:
            raise ValidationError(_(
                "The repayment for %(loan)s (%(amount)s) exceeds its balance (%(balance)s).",
                loan=self.loan_id.name, amount=self.amount_loan, balance=self.loan_id.balance))

