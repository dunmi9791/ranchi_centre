# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

ACTIVE_STATES = ('applied', 'approved', 'fees', 'disbursed')


class RanchiLoan(models.Model):
    _name = 'ranchi.loan'
    _description = 'Ranchi Loan'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(string="Loan Number", readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, index=True, tracking=True, check_company=True,
        domain="[('is_ranchi_member', '=', True), ('membership_state', '=', 'confirmed')]")
    union_id = fields.Many2one(
        'ranchi.union', string="Union", related='member_id.union_id', store=True, readonly=True, index=True)
    credit_officer_id = fields.Many2one(related='union_id.credit_officer_id', store=True, readonly=True)
    loan_type_id = fields.Many2one(
        'ranchi.loan.type', string="Loan Type", required=True, tracking=True,
        compute='_compute_loan_type_id', store=True, readonly=False, precompute=True,
        domain="[('id', '=', union_loan_type_id)]",
        help="Fixed by the member's union: each union runs a single loan type.")
    union_loan_type_id = fields.Many2one(related='union_id.loan_type_id', string="Union Loan Type")
    stage_id = fields.Many2one(
        'ranchi.loan.stage', string="Loan Stage", tracking=True,
        domain="[('loan_type_id', '=', loan_type_id)]")
    state = fields.Selection(
        [('draft', 'Draft'), ('applied', 'Applied'), ('approved', 'Approved'),
         ('fees', 'Fees Paid'), ('disbursed', 'Disbursed'), ('paid', 'Fully Paid'),
         ('written_off', 'Written Off'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)

    # ---- dates -----------------------------------------------------------
    date_application = fields.Date(
        string="Application Date", default=fields.Date.context_today, required=True, tracking=True)
    date_approval = fields.Date(readonly=True, copy=False)
    date_first_due = fields.Date(
        string="First Installment Due", tracking=True,
        compute='_compute_date_first_due', store=True, readonly=False)
    date_last_due = fields.Date(compute='_compute_date_last_due', string="Last Installment Due")
    date_disbursed = fields.Date(readonly=True, copy=False)
    date_closed = fields.Date(readonly=True, copy=False)

    # ---- amounts ---------------------------------------------------------
    amount_applied = fields.Monetary(string="Amount Applied", required=True, tracking=True)
    amount_approved = fields.Monetary(string="Amount Approved", tracking=True, copy=False)
    principal = fields.Monetary(compute='_compute_amounts', string="Principal")
    service_rate = fields.Float(related='loan_type_id.service_rate')
    service_collection = fields.Selection(related='loan_type_id.service_collection')
    service_amount = fields.Monetary(compute='_compute_amounts', string="Service Charge")
    admin_fee_amount = fields.Monetary(compute='_compute_amounts', string="Administration Fee")
    risk_premium_amount = fields.Monetary(compute='_compute_amounts', string="Risk Premium")
    fee_total = fields.Monetary(compute='_compute_amounts', string="Fees Before Disbursement")
    total_repayable = fields.Monetary(compute='_compute_amounts', string="Total Repayable")
    installment_amount = fields.Monetary(compute='_compute_amounts')
    installment_count = fields.Integer(related='loan_type_id.installment_count')
    installment_period = fields.Selection(related='loan_type_id.installment_period')

    amount_paid = fields.Monetary(compute='_compute_balance', store=True, string="Repaid")
    balance = fields.Monetary(compute='_compute_balance', store=True, string="Outstanding Balance")
    amount_overdue = fields.Monetary(compute='_compute_overdue', string="Overdue Amount")
    overdue_count = fields.Integer(compute='_compute_overdue')
    installments_paid_count = fields.Integer(compute='_compute_overdue')
    next_due_date = fields.Date(compute='_compute_overdue')

    # ---- relations -------------------------------------------------------
    installment_ids = fields.One2many('ranchi.loan.installment', 'loan_id', string="Schedule", copy=False)
    repayment_ids = fields.One2many('ranchi.loan.repayment', 'loan_id', string="Repayments", copy=False)
    move_ids = fields.One2many('account.move', 'ranchi_loan_id', string="Journal Entries", copy=False)
    fee_move_id = fields.Many2one('account.move', string="Fee Invoice", readonly=True, copy=False)
    fee_payment_state = fields.Selection(related='fee_move_id.payment_state')
    disbursement_id = fields.Many2one('ranchi.disbursement', string="Disbursement", readonly=True, copy=False)
    disbursement_state = fields.Selection(related='disbursement_id.state')
    disbursement_move_id = fields.Many2one('account.move', string="Disbursement Entry", readonly=True, copy=False)
    writeoff_move_id = fields.Many2one('account.move', string="Write-off Entry", readonly=True, copy=False)
    writeoff_reason = fields.Text(readonly=True, copy=False)

    # ---- application questionnaire ---------------------------------------
    purpose = fields.Text(string="Purpose of Loan")
    avg_monthly_income = fields.Monetary(string="Average Monthly Income")
    has_family_in_union = fields.Boolean(string="Family member registered in the union")
    family_member_name = fields.Char()
    is_indebted_elsewhere = fields.Boolean(string="Indebted to another MFB/MFI")
    indebted_amount = fields.Monetary()
    indebted_institution = fields.Char(string="MFB/MFI Name")
    last_loan_amount = fields.Monetary(string="Last Loan Received")
    last_loan_paid_date = fields.Date(string="Last Loan Fully Paid On")

    # ---- guarantor -------------------------------------------------------
    guarantor_name = fields.Char(tracking=True)
    guarantor_relationship = fields.Char(string="Relationship with Borrower")
    guarantor_phone = fields.Char(tracking=True)
    guarantor_home_address = fields.Text()
    guarantor_office_address = fields.Text()
    notes = fields.Text()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.loan') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(loan.state not in ('draft', 'cancelled') for loan in self):
            raise UserError(_("Only draft or cancelled loans can be deleted."))

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('member_id.union_id.loan_type_id')
    def _compute_loan_type_id(self):
        # Depends on member_id (a many2one) rather than the stored related union_id so the
        # field stays precomputable: a union runs exactly one loan type.
        for loan in self:
            union_type = loan.member_id.union_id.loan_type_id
            if union_type:
                loan.loan_type_id = union_type

    @api.depends('date_application', 'loan_type_id')
    def _compute_date_first_due(self):
        for loan in self:
            if loan.date_first_due or not loan.loan_type_id or not loan.date_application:
                continue
            loan.date_first_due = loan.date_application + loan.loan_type_id._period_delta()

    @api.depends('installment_ids.date_due', 'date_first_due', 'loan_type_id')
    def _compute_date_last_due(self):
        for loan in self:
            if loan.installment_ids:
                loan.date_last_due = max(loan.installment_ids.mapped('date_due'))
            elif loan.date_first_due and loan.loan_type_id:
                loan.date_last_due = loan.loan_type_id._due_dates(
                    loan.date_first_due, loan.loan_type_id.installment_count, loan.company_id)[-1]
            else:
                loan.date_last_due = False

    @api.depends('amount_applied', 'amount_approved', 'loan_type_id', 'state',
                 'loan_type_id.service_rate', 'loan_type_id.admin_charge',
                 'loan_type_id.risk_premium_rate', 'loan_type_id.installment_count',
                 'loan_type_id.service_collection', 'installment_ids.amount_total')
    def _compute_amounts(self):
        for loan in self:
            currency = loan.currency_id
            lt = loan.loan_type_id
            principal = loan.amount_approved if loan.state not in ('draft', 'applied') or loan.amount_approved \
                else loan.amount_applied
            loan.principal = principal
            service = currency.round(principal * (lt.service_rate or 0.0) / 100.0) if currency else 0.0
            risk = currency.round(principal * (lt.risk_premium_rate or 0.0) / 100.0) if currency else 0.0
            loan.service_amount = service
            loan.admin_fee_amount = lt.admin_charge or 0.0
            loan.risk_premium_amount = risk
            upfront = lt.service_collection == 'upfront'
            loan.fee_total = loan.admin_fee_amount + risk + (service if upfront else 0.0)
            if loan.installment_ids:
                loan.total_repayable = sum(loan.installment_ids.mapped('amount_total'))
            else:
                loan.total_repayable = principal + (0.0 if upfront else service)
            count = lt.installment_count or 0
            loan.installment_amount = currency.round(loan.total_repayable / count) if count and currency else 0.0

    @api.depends('installment_ids.amount_residual', 'installment_ids.amount_total', 'state')
    def _compute_balance(self):
        for loan in self:
            installments = loan.installment_ids
            if loan.state in ('paid', 'written_off', 'cancelled'):
                loan.balance = 0.0
                loan.amount_paid = sum(installments.mapped('amount_paid')) if loan.state == 'paid' else 0.0
            elif installments:
                loan.balance = sum(installments.mapped('amount_residual'))
                loan.amount_paid = sum(installments.mapped('amount_paid'))
            else:
                loan.balance = loan.total_repayable if loan.state in ACTIVE_STATES else 0.0
                loan.amount_paid = 0.0

    @api.depends('installment_ids.state', 'installment_ids.date_due', 'installment_ids.amount_residual')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for loan in self:
            insts = loan.installment_ids
            grace = loan.loan_type_id.grace_days or 0
            overdue = insts.filtered(
                lambda i: i.state != 'paid' and i.date_due and (today - i.date_due).days > grace
                and loan.state == 'disbursed')
            loan.amount_overdue = sum(overdue.mapped('amount_residual'))
            loan.overdue_count = len(overdue)
            loan.installments_paid_count = len(insts.filtered(lambda i: i.state == 'paid'))
            unpaid = insts.filtered(lambda i: i.state != 'paid').sorted('date_due')
            loan.next_due_date = unpaid[:1].date_due if unpaid else False

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('amount_applied', 'amount_approved')
    def _check_amounts(self):
        for loan in self:
            if loan.amount_applied <= 0:
                raise ValidationError(_("The amount applied must be positive."))
            if loan.amount_approved < 0:
                raise ValidationError(_("The amount approved cannot be negative."))

    @api.constrains('member_id', 'company_id')
    def _check_member_company(self):
        for loan in self:
            if loan.member_id.company_id and loan.member_id.company_id != loan.company_id:
                raise ValidationError(_("The member belongs to another branch."))

    @api.constrains('loan_type_id', 'union_id')
    def _check_loan_type_union(self):
        for loan in self:
            union_type = loan.union_id.loan_type_id
            if union_type and loan.loan_type_id != union_type:
                raise ValidationError(_(
                    "%(union)s runs %(type)s only: this loan cannot use %(other)s.",
                    union=loan.union_id.name, type=union_type.name, other=loan.loan_type_id.name))

    @api.constrains('stage_id', 'loan_type_id')
    def _check_stage_type(self):
        for loan in self:
            if loan.stage_id and loan.stage_id.loan_type_id != loan.loan_type_id:
                raise ValidationError(_("The loan stage does not belong to the selected loan type."))

    @api.onchange('loan_type_id')
    def _onchange_loan_type_id(self):
        if self.stage_id and self.stage_id.loan_type_id != self.loan_type_id:
            self.stage_id = False

    # ------------------------------------------------------------------
    # Eligibility helpers
    # ------------------------------------------------------------------
    def _check_eligibility(self, amount):
        self.ensure_one()
        member = self.member_id
        if member.membership_state != 'confirmed':
            raise UserError(_("%s is not a confirmed member.", member.display_name))
        if not member.union_id:
            raise UserError(_("%s does not belong to a union.", member.display_name))
        if member.union_id.loan_type_id and self.loan_type_id != member.union_id.loan_type_id:
            raise UserError(_(
                "%(union)s runs %(type)s only.", union=member.union_id.name,
                type=member.union_id.loan_type_id.name))
        if self.company_id.ranchi_one_active_loan:
            others = self.search_count([
                ('member_id', '=', member.id), ('id', '!=', self.id), ('state', 'in', ACTIVE_STATES)])
            if others:
                raise UserError(_("%s already has an active loan.", member.display_name))
        lt = self.loan_type_id
        if lt.min_amount and amount < lt.min_amount:
            raise UserError(_("The amount is below the minimum for %s.", lt.name))
        if lt.max_amount and amount > lt.max_amount:
            raise UserError(_("The amount exceeds the maximum for %s.", lt.name))
        stage = self.stage_id
        if stage:
            if member.loan_cycle < stage.min_cycle:
                raise UserError(_(
                    "%(member)s has repaid %(done)s loan(s); stage %(stage)s requires %(need)s.",
                    member=member.display_name, done=member.loan_cycle, stage=stage.name, need=stage.min_cycle))
            if amount > stage.max_principal:
                raise UserError(_("The amount exceeds the ceiling of stage %s.", stage.name))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_apply(self):
        for loan in self:
            if loan.state != 'draft':
                raise UserError(_("Only draft loans can be submitted."))
            loan._check_eligibility(loan.amount_applied)
        self.write({'state': 'applied'})

    def action_approve(self):
        for loan in self:
            if loan.state != 'applied':
                raise UserError(_("Only applied loans can be approved."))
            if not loan.amount_approved:
                loan.amount_approved = loan.amount_applied
            if not loan.date_first_due:
                raise UserError(_("Set the first installment due date before approving."))
            loan._check_eligibility(loan.amount_approved)
            loan._generate_schedule()
            loan.write({'state': 'approved', 'date_approval': fields.Date.context_today(self)})
            if loan.currency_id.is_zero(loan.fee_total):
                loan._on_fees_paid()

    def action_reset_to_draft(self):
        for loan in self:
            if loan.state not in ('applied', 'approved', 'cancelled'):
                raise UserError(_("Only applied, approved or cancelled loans can go back to draft."))
            if loan.fee_move_id and loan.fee_move_id.state != 'cancel':
                raise UserError(_("Cancel the fee invoice first."))
            loan.installment_ids.unlink()
        self.write({'state': 'draft', 'date_approval': False, 'amount_approved': 0.0})

    def action_cancel(self):
        for loan in self:
            if loan.state not in ('draft', 'applied', 'approved', 'fees'):
                raise UserError(_("Only loans that are not yet disbursed can be cancelled."))
            if loan.disbursement_id and loan.disbursement_id.state not in ('cancelled', 'failed'):
                raise UserError(_("Cancel the pending disbursement first."))
            invoice = loan.fee_move_id
            if invoice and invoice.state != 'cancel':
                if invoice.payment_state not in ('not_paid',):
                    raise UserError(_("The fee invoice has already been paid; refund it before cancelling."))
                if invoice.state == 'posted':
                    invoice.button_draft()
                invoice.button_cancel()
            loan.installment_ids.unlink()
        self.write({'state': 'cancelled'})

    def _generate_schedule(self):
        Installment = self.env['ranchi.loan.installment']
        for loan in self:
            if loan.installment_ids.filtered('move_line_id'):
                raise UserError(_("The schedule of %s is already booked and cannot be regenerated.", loan.name))
            loan.installment_ids.unlink()
            lt = loan.loan_type_id
            count = lt.installment_count
            currency = loan.currency_id
            principal = loan.amount_approved
            service = currency.round(principal * lt.service_rate / 100.0) if lt.service_collection == 'spread' else 0.0
            base_principal = currency.round(principal / count)
            base_service = currency.round(service / count)
            dates = lt._due_dates(loan.date_first_due, count, loan.company_id)
            vals_list = []
            for i, due in enumerate(dates, start=1):
                p = base_principal if i < count else currency.round(principal - base_principal * (count - 1))
                s = base_service if i < count else currency.round(service - base_service * (count - 1))
                vals_list.append({
                    'loan_id': loan.id,
                    'sequence': i,
                    'date_due': due,
                    'amount_principal': p,
                    'amount_service': s,
                })
            Installment.create(vals_list)

    def _reschedule_for_holidays(self):
        """Move the unpaid installments due from today onwards off public holidays (and
        weekends for daily loans). Paid and past installments are left where they are.
        Returns the loans whose schedule changed."""
        today = fields.Date.context_today(self)
        changed = self.browse()
        for loan in self.filtered(lambda l: l.state in ('approved', 'fees', 'disbursed')):
            lt = loan.loan_type_id
            block = loan.installment_ids.filtered(
                lambda i: i.state != 'paid' and i.date_due >= today).sorted('sequence')
            if not block:
                continue
            if lt.installment_period == 'monthly' and loan.date_first_due:
                # Monthly loans keep their day of the month: recompute from the nominal dates.
                all_dates = lt._due_dates(loan.date_first_due, max(block.mapped('sequence')), loan.company_id)
                dates = [all_dates[inst.sequence - 1] for inst in block]
            else:
                dates = lt._due_dates(block[0].date_due, len(block), loan.company_id)
            moves = []
            for inst, new_date in zip(block, dates):
                if inst.date_due == new_date:
                    continue
                moves.append((inst.sequence, inst.date_due, new_date))
                inst.date_due = new_date
                if inst.move_line_id:
                    inst.move_line_id.sudo().date_maturity = new_date
            if moves:
                changed |= loan
                lines = Markup().join(
                    Markup("<li>%s</li>") % _("Installment %(seq)s: %(old)s → %(new)s", seq=seq, old=old, new=new)
                    for seq, old, new in moves)
                loan.message_post(body=Markup("%s<ul>%s</ul>") % (_("Schedule moved for public holidays:"), lines))
        return changed

    def action_reschedule_holidays(self):
        if self._reschedule_for_holidays():
            return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': 'info', 'message': _("No upcoming installment falls on a public holiday.")},
        }

    def action_create_fee_invoice(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Fees are invoiced once the loan is approved."))
        if self.fee_move_id and self.fee_move_id.state != 'cancel':
            return self._action_open_move(self.fee_move_id)
        if self.currency_id.is_zero(self.fee_total):
            self._on_fees_paid()
            return True
        company = self.company_id
        journal = company._ranchi_require('ranchi_fee_journal_id')
        lines = []

        def add_line(product_field, label, amount):
            if self.currency_id.is_zero(amount):
                return
            product = company._ranchi_require(product_field)
            lines.append((0, 0, {
                'product_id': product.id,
                'name': f"{label} - {self.name}",
                'quantity': 1.0,
                'price_unit': amount,
                'tax_ids': [(6, 0, product.taxes_id.filtered(lambda t: t.company_id == company).ids)],
            }))

        add_line('ranchi_admin_fee_product_id', _("Loan Administration Fee"), self.admin_fee_amount)
        add_line('ranchi_risk_premium_product_id', _("Risk Premium"), self.risk_premium_amount)
        if self.service_collection == 'upfront':
            add_line('ranchi_service_charge_product_id', _("Service Charge"), self.service_amount)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.member_id.id,
            'company_id': company.id,
            'journal_id': journal.id,
            'currency_id': self.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': self.name,
            'ranchi_move_type': 'loan_fee',
            'ranchi_loan_id': self.id,
            'invoice_line_ids': lines,
        })
        invoice.action_post()
        self.fee_move_id = invoice
        self.message_post(body=_("Fee invoice %s created.", invoice.name))
        return self._action_open_move(invoice)

    def _on_fees_paid(self):
        for loan in self:
            if loan.state == 'approved':
                loan.write({'state': 'fees'})
                loan.message_post(body=_("Fees settled. The loan is ready for disbursement."))

    def action_request_disbursement(self):
        self.ensure_one()
        if self.state != 'fees':
            raise UserError(_("The loan must have its fees paid before disbursement."))
        if self.disbursement_id and self.disbursement_id.state not in ('cancelled', 'failed'):
            return self.disbursement_id._action_open()
        company = self.company_id
        company._ranchi_require('ranchi_loan_receivable_account_id')
        company._ranchi_require('ranchi_disbursement_journal_id')
        if self.service_collection == 'spread' and not self.currency_id.is_zero(self.service_amount):
            company._ranchi_require('ranchi_service_income_account_id')
        bank = self.member_id.bank_ids[:1]
        disbursement = self.env['ranchi.disbursement'].create({
            'loan_id': self.id,
            'amount': self.amount_approved,
            'bank_account_id': bank.id,
            'provider': company.ranchi_disbursement_provider,
            'journal_id': company.ranchi_disbursement_journal_id.id,
        })
        self.disbursement_id = disbursement
        return disbursement._action_open()

    def _on_disbursed(self, disbursement):
        """Book the loan: Dr Loans Receivable per installment / Cr Bank (principal) / Cr Service Income."""
        self.ensure_one()
        if self.state != 'fees':
            raise UserError(_("Loan %s is not awaiting disbursement.", self.name))
        company = self.company_id
        receivable = company._ranchi_require('ranchi_loan_receivable_account_id')
        if not receivable.reconcile:
            raise UserError(_("The Loans Receivable account must allow reconciliation."))
        journal = disbursement.journal_id or company._ranchi_require('ranchi_disbursement_journal_id')
        bank_account = journal.default_account_id
        if not bank_account:
            raise UserError(_("Journal %s has no default account.", journal.name))
        partner = self.member_id
        date = fields.Date.context_today(self)
        lines = []
        for inst in self.installment_ids.sorted('sequence'):
            lines.append((0, 0, {
                'name': _("%(loan)s installment %(seq)s/%(count)s",
                          loan=self.name, seq=inst.sequence, count=len(self.installment_ids)),
                'account_id': receivable.id,
                'partner_id': partner.id,
                'debit': inst.amount_total,
                'credit': 0.0,
                'date_maturity': inst.date_due,
                'ranchi_installment_id': inst.id,
            }))
        lines.append((0, 0, {
            'name': _("Disbursement %s", self.name),
            'account_id': bank_account.id,
            'partner_id': partner.id,
            'debit': 0.0,
            'credit': self.amount_approved,
        }))
        service_total = sum(self.installment_ids.mapped('amount_service'))
        if not self.currency_id.is_zero(service_total):
            lines.append((0, 0, {
                'name': _("Service charge %s", self.name),
                'account_id': company._ranchi_require('ranchi_service_income_account_id').id,
                'partner_id': partner.id,
                'debit': 0.0,
                'credit': service_total,
            }))
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': company.id,
            'date': date,
            'ref': _("Loan disbursement %s", self.name),
            'ranchi_move_type': 'disbursement',
            'ranchi_loan_id': self.id,
            'line_ids': lines,
        })
        move.action_post()
        for line in move.line_ids.filtered('ranchi_installment_id'):
            line.ranchi_installment_id.move_line_id = line
        self.write({
            'state': 'disbursed',
            'date_disbursed': date,
            'disbursement_move_id': move.id,
        })
        self.message_post(body=_("Loan disbursed. Journal entry %s posted.", move.name))

    # ------------------------------------------------------------------
    # Repayment helpers used by collections, lapse adjustments and the API
    # ------------------------------------------------------------------
    def _allocate_repayment(self, amount):
        """Split *amount* over unpaid installments, oldest first.

        Returns a list of (installment, amount) tuples.
        """
        self.ensure_one()
        currency = self.currency_id
        if self.state != 'disbursed':
            raise UserError(_("Loan %s is not disbursed; nothing to repay.", self.name))
        if float_compare(amount, 0.0, precision_rounding=currency.rounding) <= 0:
            raise UserError(_("The repayment amount must be positive."))
        if float_compare(amount, self.balance, precision_rounding=currency.rounding) > 0:
            raise UserError(_(
                "The repayment of %(amount)s exceeds the outstanding balance %(balance)s of loan %(loan)s.",
                amount=amount, balance=self.balance, loan=self.name))
        allocations = []
        remaining = amount
        for inst in self.installment_ids.filtered(lambda i: i.state != 'paid').sorted('date_due'):
            if float_is_zero(remaining, precision_rounding=currency.rounding):
                break
            portion = min(inst.amount_residual, remaining)
            if float_is_zero(portion, precision_rounding=currency.rounding):
                continue
            allocations.append((inst, currency.round(portion)))
            remaining -= portion
        return allocations

    def _prepare_repayment_credit_lines(self, allocations, label=None):
        self.ensure_one()
        receivable = self.company_id._ranchi_require('ranchi_loan_receivable_account_id')
        lines = []
        for inst, amount in allocations:
            lines.append((0, 0, {
                'name': label or _("Repayment %(loan)s installment %(seq)s", loan=self.name, seq=inst.sequence),
                'account_id': receivable.id,
                'partner_id': self.member_id.id,
                'debit': 0.0,
                'credit': amount,
                'ranchi_installment_id': inst.id,
            }))
        return lines

    def _register_repayments(self, move, allocations, source, **extra):
        """Create repayment records for a posted *move*, reconcile them and close the loan if settled."""
        self.ensure_one()
        Repayment = self.env['ranchi.loan.repayment']
        for inst, amount in allocations:
            credit_line = move.line_ids.filtered(
                lambda l: l.ranchi_installment_id == inst and l.credit > 0)[:1]
            Repayment.create({
                'loan_id': self.id,
                'installment_id': inst.id,
                'date': move.date,
                'amount': amount,
                'source': source,
                'move_line_id': credit_line.id,
                **extra,
            })
            if inst.move_line_id and credit_line:
                (inst.move_line_id | credit_line).reconcile()
        self.installment_ids.invalidate_recordset(['amount_residual', 'amount_paid', 'state'])
        self._check_fully_paid()

    def _check_fully_paid(self):
        for loan in self:
            if loan.state == 'disbursed' and loan.installment_ids and \
                    all(inst.state == 'paid' for inst in loan.installment_ids):
                loan.write({'state': 'paid', 'date_closed': fields.Date.context_today(self)})
                loan.message_post(body=_("Loan fully repaid."))

    def _write_off(self, reason):
        self.ensure_one()
        if self.state != 'disbursed':
            raise UserError(_("Only disbursed loans can be written off."))
        company = self.company_id
        writeoff_account = company._ranchi_require('ranchi_writeoff_account_id')
        journal = company._ranchi_require('ranchi_savings_journal_id')
        allocations = [(inst, inst.amount_residual) for inst in self.installment_ids
                       if not self.currency_id.is_zero(inst.amount_residual)]
        if not allocations:
            raise UserError(_("Nothing left to write off."))
        total = sum(a for _i, a in allocations)
        lines = self._prepare_repayment_credit_lines(allocations, label=_("Write-off %s", self.name))
        lines.append((0, 0, {
            'name': _("Write-off %s", self.name),
            'account_id': writeoff_account.id,
            'partner_id': self.member_id.id,
            'debit': total,
            'credit': 0.0,
        }))
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': company.id,
            'date': fields.Date.context_today(self),
            'ref': _("Loan write-off %s", self.name),
            'ranchi_move_type': 'writeoff',
            'ranchi_loan_id': self.id,
            'line_ids': lines,
        })
        move.action_post()
        self._register_repayments(move, allocations, 'writeoff')
        self.write({'state': 'written_off', 'writeoff_move_id': move.id, 'writeoff_reason': reason,
                    'date_closed': fields.Date.context_today(self)})
        self.message_post(body=_("Loan written off: %s", reason))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _action_open_move(self, move):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_fee_invoice(self):
        self.ensure_one()
        return self._action_open_move(self.fee_move_id)

    def action_view_moves(self):
        self.ensure_one()
        return {
            'name': _('Journal Entries'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('ranchi_loan_id', '=', self.id)],
        }

    def action_open_writeoff_wizard(self):
        self.ensure_one()
        return {
            'name': _('Write Off Loan'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.loan.writeoff.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_loan_id': self.id},
        }

    def action_view_disbursement(self):
        self.ensure_one()
        return self.disbursement_id._action_open()
