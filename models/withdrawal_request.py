# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RanchiWithdrawalRequest(models.Model):
    _name = 'ranchi.withdrawal.request'
    _description = 'Ranchi Savings Withdrawal Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'request_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string="Reference", readonly=True, copy=False, default=lambda self: _('New'))
    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, index=True, tracking=True, check_company=True,
        domain="[('is_ranchi_member', '=', True), ('membership_state', '=', 'confirmed')]")
    union_id = fields.Many2one(related='member_id.union_id', store=True, readonly=True, index=True)
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    request_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    amount = fields.Monetary(required=True, tracking=True)
    reason = fields.Text()
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved_l1', 'First Approval'),
         ('approved', 'Approved'), ('paid', 'Paid'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    approval_levels = fields.Selection(related='company_id.ranchi_withdrawal_approval_levels')
    savings_balance = fields.Monetary(related='member_id.savings_balance')
    savings_available = fields.Monetary(related='member_id.savings_available')
    fee_amount = fields.Monetary(compute='_compute_fee_amount', string="Withdrawal Fee")
    net_amount = fields.Monetary(compute='_compute_fee_amount', string="Net Payout")
    hold_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    payment_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    fee_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    journal_id = fields.Many2one(
        'account.journal', string="Payout Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    paid_date = fields.Date(readonly=True, copy=False)
    first_approver_id = fields.Many2one('res.users', readonly=True, copy=False)
    approver_id = fields.Many2one('res.users', readonly=True, copy=False)
    rejection_reason = fields.Text(copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.withdrawal.request') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(req.state not in ('draft', 'cancelled') for req in self):
            raise UserError(_("Only draft or cancelled requests can be deleted."))

    @api.depends('amount', 'company_id')
    def _compute_fee_amount(self):
        for req in self:
            company = req.company_id
            fee = req.amount * (company.ranchi_withdrawal_fee_percent or 0.0) / 100.0 + \
                (company.ranchi_withdrawal_flat_fee or 0.0)
            req.fee_amount = req.currency_id.round(fee) if req.currency_id else fee
            req.net_amount = req.amount - req.fee_amount

    @api.constrains('amount')
    def _check_amount(self):
        for req in self:
            if req.amount <= 0:
                raise ValidationError(_("The withdrawal amount must be positive."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        Tx = self.env['ranchi.savings.transaction']
        for req in self:
            if req.state != 'draft':
                raise UserError(_("Only draft requests can be submitted."))
            company = req.company_id
            total_out = req.amount + req.fee_amount
            remaining = req.member_id.savings_available - total_out
            if req.currency_id.compare_amounts(remaining, company.ranchi_savings_min_balance) < 0:
                raise UserError(_(
                    "%(member)s has %(available)s available; this request (%(amount)s plus %(fee)s fee) "
                    "would leave less than the minimum balance of %(min)s.",
                    member=req.member_id.display_name, available=req.member_id.savings_available,
                    amount=req.amount, fee=req.fee_amount, min=company.ranchi_savings_min_balance))
            hold = Tx.create({
                'member_id': req.member_id.id,
                'company_id': company.id,
                'date': req.request_date,
                'amount': total_out,
                'type': 'hold',
                'origin': 'withdrawal',
                'withdrawal_request_id': req.id,
                'note': _("Hold for %s", req.name),
            })
            hold.action_post()
            req.write({'hold_transaction_id': hold.id, 'state': 'submitted'})

    def action_approve(self):
        for req in self:
            if req.state == 'submitted' and req.approval_levels == '2':
                req.write({'state': 'approved_l1', 'first_approver_id': self.env.user.id})
            elif req.state in ('submitted', 'approved_l1'):
                if req.state == 'approved_l1' and req.first_approver_id == self.env.user \
                        and not self.env.user.has_group('ranchi_centre.group_ranchi_manager'):
                    raise UserError(_("A different user must give the second approval."))
                req.write({'state': 'approved', 'approver_id': self.env.user.id})
            else:
                raise UserError(_("Only submitted requests can be approved."))

    def action_reject(self):
        for req in self:
            if req.state not in ('submitted', 'approved_l1', 'approved'):
                raise UserError(_("Only pending requests can be rejected."))
            req._release_hold()
            req.state = 'rejected'

    def action_cancel(self):
        for req in self:
            if req.state not in ('draft', 'submitted', 'approved_l1', 'approved'):
                raise UserError(_("Paid or rejected requests cannot be cancelled."))
            req._release_hold()
            req.state = 'cancelled'

    def action_reset_to_draft(self):
        for req in self:
            if req.state not in ('rejected', 'cancelled'):
                raise UserError(_("Only rejected or cancelled requests can be reset."))
            req.state = 'draft'

    def _release_hold(self):
        for req in self:
            hold = req.hold_transaction_id
            if hold and hold.state == 'posted':
                hold.state = 'cancelled'

    def action_open_pay_wizard(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Only approved requests can be paid."))
        return {
            'name': _('Pay Withdrawal'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.withdrawal.pay.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_request_id': self.id,
                'default_journal_id': (self.journal_id or self.company_id.ranchi_collection_journal_id).id,
            },
        }

    def action_pay(self, journal=None, pay_date=None):
        Tx = self.env['ranchi.savings.transaction']
        for req in self:
            if req.state != 'approved':
                raise UserError(_("Only approved requests can be paid."))
            journal = journal or req.journal_id or req.company_id._ranchi_require('ranchi_collection_journal_id')
            pay_date = pay_date or fields.Date.context_today(self)
            payout = Tx.create({
                'member_id': req.member_id.id,
                'company_id': req.company_id.id,
                'date': pay_date,
                'amount': req.amount,
                'type': 'withdrawal',
                'origin': 'withdrawal',
                'journal_id': journal.id,
                'withdrawal_request_id': req.id,
                'note': _("Withdrawal %s", req.name),
            })
            payout.action_post()
            fee_tx = Tx
            if not req.currency_id.is_zero(req.fee_amount):
                fee_tx = Tx.create({
                    'member_id': req.member_id.id,
                    'company_id': req.company_id.id,
                    'date': pay_date,
                    'amount': req.fee_amount,
                    'type': 'fee',
                    'origin': 'withdrawal',
                    'withdrawal_request_id': req.id,
                    'note': _("Withdrawal fee %s", req.name),
                })
                fee_tx.action_post()
            req._release_hold()
            req.write({
                'state': 'paid',
                'paid_date': pay_date,
                'journal_id': journal.id,
                'payment_transaction_id': payout.id,
                'fee_transaction_id': fee_tx.id if fee_tx else False,
            })
