# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

MANAGER_GROUP = 'ranchi_centre.group_ranchi_manager'
GENERAL_MANAGER_GROUP = 'ranchi_centre.group_ranchi_general_manager'
# Fields only a manager may set; the officer's part is the request itself.
MANAGER_FIELDS = {'confirmed_by_id', 'confirmed_date', 'cash_amount', 'move_id', 'savings_transaction_id',
                  'rejection_reason'}


class RanchiLapseAdjustment(models.Model):
    """Pay a loan from the member's savings.

    A credit officer requests it (from the field app or the backend) and the requested savings are put
    on hold; a manager of the loan's product confirms it, which posts the journal entry. With
    *Settle in full* the loan is closed and any balance not covered by savings is collected in cash.
    """
    _name = 'ranchi.lapse.adjustment'
    _description = 'Ranchi Savings Adjustment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(string="Reference", readonly=True, copy=False, default=lambda self: _('New'))
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, tracking=True, check_company=True,
        domain="[('is_ranchi_member', '=', True)]")
    union_id = fields.Many2one(related='member_id.union_id', store=True)
    loan_id = fields.Many2one(
        'ranchi.loan', string="Loan", required=True, tracking=True, check_company=True,
        domain="[('member_id', '=', member_id), ('state', '=', 'disbursed')]")
    loan_type_id = fields.Many2one(
        related='loan_id.loan_type_id', string="Loan Type", store=True, index=True,
        help="Decides which product manager confirms the adjustment.")
    settle_in_full = fields.Boolean(
        default=True, tracking=True,
        help="Close the loan: savings cover what they can and the rest is paid in cash. "
             "Untick to only apply the savings amount to the oldest installments.")
    savings_available = fields.Monetary(compute='_compute_amounts')
    loan_balance = fields.Monetary(compute='_compute_amounts')
    savings_used = fields.Monetary(
        compute='_compute_savings_used', store=True, readonly=False, tracking=True,
        help="Portion of the loan paid from savings. Defaults to the maximum possible.")
    shortfall = fields.Monetary(compute='_compute_amounts', string="Cash Shortfall")
    cash_amount = fields.Monetary(string="Cash Received", readonly=True, copy=False,
                                  help="Cash shortfall collected when the adjustment was confirmed.")
    journal_id = fields.Many2one(
        'account.journal', string="Shortfall Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Requested'), ('done', 'Posted'),
         ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    requested_by_id = fields.Many2one('res.users', string="Requested By", readonly=True, copy=False)
    submitted_date = fields.Datetime(string="Requested On", readonly=True, copy=False)
    confirmed_by_id = fields.Many2one('res.users', string="Confirmed By", readonly=True, copy=False, tracking=True)
    confirmed_date = fields.Datetime(string="Confirmed On", readonly=True, copy=False)
    rejection_reason = fields.Char(copy=False, tracking=True)
    hold_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    savings_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    note = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            for vals in vals_list:
                if vals.get('state', 'draft') != 'draft' or MANAGER_FIELDS & set(vals):
                    raise AccessError(_("Only a manager can confirm a savings adjustment."))
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.lapse.adjustment') or '/'
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            if vals.get('state') in ('done', 'rejected') or MANAGER_FIELDS & set(vals):
                raise AccessError(_("Only a manager can confirm or reject a savings adjustment."))
            if any(rec.state != 'draft' for rec in self):
                raise UserError(_("This adjustment has been requested; cancel it or ask a manager before editing."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(rec.state not in ('draft', 'cancelled') for rec in self):
            raise UserError(_("Only draft or cancelled adjustments can be deleted."))

    @api.depends('member_id', 'loan_id')
    def _compute_savings_used(self):
        for rec in self:
            # once requested, the amount is what the officer asked for
            rec.savings_used = rec._max_from_savings() if rec.state == 'draft' else rec.savings_used

    @api.depends('member_id', 'loan_id', 'savings_used', 'settle_in_full', 'state', 'hold_transaction_id.state')
    def _compute_amounts(self):
        for rec in self:
            rec.savings_available = rec._savings_available()
            if rec.state in ('draft', 'submitted'):
                rec.loan_balance = rec.loan_id.balance
                rec.shortfall = max(0.0, rec.loan_balance - rec.savings_used) if rec.settle_in_full else 0.0
            else:
                rec.loan_balance = 0.0
                rec.shortfall = 0.0

    def _savings_available(self):
        """Available savings, counting this request's own hold as still available to it."""
        self.ensure_one()
        own_hold = self.hold_transaction_id
        held = own_hold.amount if own_hold and own_hold.state == 'posted' else 0.0
        return self.member_id.savings_available + held

    def _max_from_savings(self):
        self.ensure_one()
        return max(0.0, min(self._savings_available(), self.loan_id.balance))

    @api.constrains('savings_used')
    def _check_savings_used(self):
        for rec in self:
            if rec.savings_used < 0:
                raise ValidationError(_("Savings used cannot be negative."))

    @api.constrains('member_id', 'loan_id')
    def _check_loan_member(self):
        for rec in self:
            if rec.loan_id.member_id != rec.member_id:
                raise ValidationError(_("Loan %(loan)s does not belong to %(member)s.",
                                        loan=rec.loan_id.name, member=rec.member_id.display_name))

    def _validate_amounts(self):
        for rec in self:
            currency = rec.currency_id
            loan = rec.loan_id
            if loan.state != 'disbursed':
                raise UserError(_("Loan %s is not outstanding.", loan.name))
            if currency.is_zero(loan.balance):
                raise UserError(_("The loan has no outstanding balance."))
            if currency.compare_amounts(rec.savings_used, 0.0) <= 0:
                raise UserError(_("Enter the amount to take from savings."))
            if currency.compare_amounts(rec.savings_used, rec._savings_available()) > 0:
                raise UserError(_(
                    "%(member)s only has %(available)s available in savings.",
                    member=rec.member_id.display_name, available=currency.format(rec._savings_available())))
            if currency.compare_amounts(rec.savings_used, loan.balance) > 0:
                raise UserError(_(
                    "%(amount)s from savings is more than the %(balance)s left on loan %(loan)s.",
                    amount=currency.format(rec.savings_used), balance=currency.format(loan.balance), loan=loan.name))

    def _check_manager(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a manager can confirm or reject a savings adjustment."))
        if user.has_group(GENERAL_MANAGER_GROUP):
            return
        for rec in self:
            if user not in rec.sudo().loan_type_id.manager_ids:
                raise AccessError(_(
                    "%(ref)s is a %(type)s loan; only that product's managers can confirm it.",
                    ref=rec.name, type=rec.sudo().loan_type_id.name))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        """Credit officer requests the adjustment; the savings are held until a manager decides."""
        Tx = self.env['ranchi.savings.transaction']
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft adjustments can be requested."))
            rec._validate_amounts()
            hold = Tx.create({
                'member_id': rec.member_id.id,
                'company_id': rec.company_id.id,
                'date': rec.date,
                'amount': rec.savings_used,
                'type': 'hold',
                'origin': 'lapse',
                'lapse_id': rec.id,
                'note': _("Hold for %(ref)s on loan %(loan)s", ref=rec.name, loan=rec.loan_id.name),
            })
            hold.action_post()
            rec.write({
                'state': 'submitted',
                'hold_transaction_id': hold.id,
                'requested_by_id': self.env.uid,
                'submitted_date': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "%(user)s requests %(amount)s from savings for loan %(loan)s.",
                user=self.env.user.name, amount=rec.currency_id.format(rec.savings_used), loan=rec.loan_id.name))
        return True

    def action_confirm(self):
        """Manager confirms: post the journal entry and apply it to the loan."""
        self._check_manager()
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                raise UserError(_("Only draft or requested adjustments can be confirmed."))
            rec._validate_amounts()
            if not rec.currency_id.is_zero(rec.shortfall) and not rec.journal_id:
                raise UserError(_("Select the journal receiving the cash shortfall."))
        # Journal entries need accounting rights that product managers do not have.
        for rec in self.sudo():
            rec._post_adjustment(self.env.uid)
        return True

    def _post_adjustment(self, confirmed_by):
        self.ensure_one()
        Tx = self.env['ranchi.savings.transaction']
        loan = self.loan_id
        company = self.company_id
        currency = self.currency_id
        savings_used = self.savings_used
        shortfall = self.shortfall
        self._release_hold()
        allocations = loan._allocate_repayment(savings_used + shortfall)
        lines = loan._prepare_repayment_credit_lines(allocations, label=_("Savings adjustment %s", self.name))
        journal = self.journal_id if not currency.is_zero(shortfall) else company._ranchi_require('ranchi_savings_journal_id')
        liability = company._ranchi_require('ranchi_savings_liability_account_id')
        lines.append((0, 0, {
            'name': _("%(ref)s paid from savings", ref=self.name),
            'account_id': liability.id,
            'partner_id': self.member_id.id,
            'debit': savings_used,
            'credit': 0.0,
        }))
        if not currency.is_zero(shortfall):
            cash_account = self.journal_id.default_account_id
            if not cash_account:
                raise UserError(_("Journal %s has no default account.", self.journal_id.name))
            lines.append((0, 0, {
                'name': _("%(ref)s cash shortfall", ref=self.name),
                'account_id': cash_account.id,
                'partner_id': self.member_id.id,
                'debit': shortfall,
                'credit': 0.0,
            }))
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': company.id,
            'date': self.date,
            'ref': _("Savings adjustment %(ref)s - %(loan)s", ref=self.name, loan=loan.name),
            'ranchi_move_type': 'lapse',
            'ranchi_loan_id': loan.id,
            'ranchi_member_id': self.member_id.id,
            'line_ids': lines,
        })
        move.action_post()
        loan._register_repayments(move, allocations, 'lapse', lapse_id=self.id)
        liability_line = move.line_ids.filtered(lambda l: l.account_id == liability)[:1]
        tx = Tx.create({
            'member_id': self.member_id.id,
            'company_id': company.id,
            'date': self.date,
            'amount': savings_used,
            'type': 'withdrawal',
            'origin': 'lapse',
            'lapse_id': self.id,
            'move_id': move.id,
            'move_line_id': liability_line.id,
            'note': _("Applied to loan %s", loan.name),
            'state': 'posted',
        })
        self.write({
            'state': 'done',
            'move_id': move.id,
            'savings_transaction_id': tx.id,
            'savings_used': savings_used,
            'cash_amount': shortfall,
            'confirmed_by_id': confirmed_by,
            'confirmed_date': fields.Datetime.now(),
        })
        self.message_post(body=_("Loan %(loan)s paid from savings. Journal entry %(move)s.", loan=loan.name, move=move.name))

    def action_reject(self):
        self._check_manager()
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only requested adjustments can be rejected."))
        for rec in self.sudo():
            rec._release_hold()
            rec.state = 'rejected'
            rec.message_post(body=_("Rejected: %s", rec.rejection_reason or _("no reason given")))
        return True

    def action_cancel(self):
        """The requesting officer (or a manager) withdraws the request before it is confirmed."""
        self.check_access('write')
        for rec in self:
            if rec.state not in ('draft', 'submitted'):
                raise UserError(_("Only draft or requested adjustments can be cancelled."))
        for rec in self.sudo():
            rec._release_hold()
            rec.state = 'cancelled'
        return True

    def action_reset_to_draft(self):
        self.check_access('write')
        for rec in self:
            if rec.state not in ('rejected', 'cancelled'):
                raise UserError(_("Only rejected or cancelled adjustments can be reset."))
        self.sudo().write({'state': 'draft', 'hold_transaction_id': False, 'rejection_reason': False})
        return True

    def _release_hold(self):
        for rec in self:
            hold = rec.hold_transaction_id
            if hold and hold.state == 'posted':
                hold.state = 'cancelled'

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }
