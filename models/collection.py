# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

MANAGER_GROUP = 'ranchi_centre.group_ranchi_manager'
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
    amount_total = fields.Monetary(compute='_compute_totals', store=True, string="Total Collected")
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    note = fields.Text()
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

    @api.depends('line_ids.amount_loan', 'line_ids.amount_savings')
    def _compute_totals(self):
        for col in self:
            col.line_count = len(col.line_ids)
            col.amount_loan_total = sum(col.line_ids.mapped('amount_loan'))
            col.amount_savings_total = sum(col.line_ids.mapped('amount_savings'))
            col.amount_total = col.amount_loan_total + col.amount_savings_total

    def action_fill_due_installments(self):
        """Add one line per member of the union with an installment due on or before the collection date."""
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
        if lines:
            self.write({'line_ids': lines})
        return True

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a manager can confirm the cash and post a collection."))

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
        """Manager confirms the cash counted matches the collection, then posts it."""
        self._check_manager()
        for col in self:
            if col.state != 'submitted':
                raise UserError(_("The credit officer must submit the collection before the cash is confirmed."))
            if col.currency_id.compare_amounts(col.amount_received, col.amount_total) != 0:
                raise UserError(_(
                    "Cash received (%(received)s) does not match the total collected (%(total)s) on %(ref)s. "
                    "Correct the count or return the collection to the officer.",
                    received=col.currency_id.format(col.amount_received),
                    total=col.currency_id.format(col.amount_total), ref=col.name))
        self.write({'received_by_id': self.env.uid, 'received_date': fields.Datetime.now()})
        return self.action_post()

    def action_post(self):
        self._check_manager()
        for col in self:
            if col.state != 'submitted':
                raise UserError(_("Only collections whose cash has been submitted can be posted."))
            if not col.line_ids:
                raise UserError(_("Add at least one line before posting."))
            if not col.received_by_id:
                raise UserError(_("Confirm the cash received before posting %s.", col.name))
            # The journal entry needs accounting rights the posting user may not have, so the
            # posting runs as superuser once the manager check above has passed.
            col.sudo()._post()
        return True

    def _post(self):
        self.ensure_one()
        company = self.company_id
        currency = self.currency_id
        cash_account = self.journal_id.default_account_id
        if not cash_account:
            raise UserError(_("Journal %s has no default account.", self.journal_id.name))
        liability = None
        if not currency.is_zero(self.amount_savings_total):
            liability = company._ranchi_require('ranchi_savings_liability_account_id')

        lines = [(0, 0, {
            'name': _("Collection %(ref)s - %(union)s", ref=self.name, union=self.union_id.name),
            'account_id': cash_account.id,
            'debit': self.amount_total,
            'credit': 0.0,
        })]
        allocations_by_line = {}
        for line in self.line_ids:
            line._validate()
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

        Tx = self.env['ranchi.savings.transaction']
        for line in self.line_ids:
            if line in allocations_by_line:
                line.loan_id._register_repayments(
                    move, allocations_by_line[line], 'collection', collection_line_id=line.id)
            if not currency.is_zero(line.amount_savings):
                liability_line = move.line_ids.filtered(
                    lambda l: l.account_id == liability and l.partner_id == line.member_id and l.credit)[:1]
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
        self.write({'state': 'posted', 'move_id': move.id})
        self.message_post(body=_("Collection posted. Journal entry %s.", move.name))

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
        self._check_collection_editable()
        return super().write(vals)

    def unlink(self):
        self._check_collection_editable()
        return super().unlink()

    def _check_collection_editable(self):
        if self.env.su:
            return
        if any(line.collection_id.state != 'draft' for line in self):
            raise UserError(_("Lines can only be changed while the collection is a draft."))

    @api.depends('amount_loan', 'amount_savings')
    def _compute_amount_total(self):
        for line in self:
            line.amount_total = line.amount_loan + line.amount_savings

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

    @api.onchange('loan_id')
    def _onchange_loan_id(self):
        if self.loan_id and not self.amount_loan:
            self.amount_loan = self.amount_due

    @api.constrains('amount_loan', 'amount_savings', 'loan_id', 'member_id')
    def _check_line(self):
        for line in self:
            line._validate()

    def _validate(self):
        self.ensure_one()
        currency = self.currency_id
        if self.amount_loan < 0 or self.amount_savings < 0:
            raise ValidationError(_("Amounts cannot be negative."))
        if currency and currency.is_zero(self.amount_loan) and currency.is_zero(self.amount_savings):
            raise ValidationError(_("Enter a loan repayment or a savings deposit for %s.", self.member_id.display_name))
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
