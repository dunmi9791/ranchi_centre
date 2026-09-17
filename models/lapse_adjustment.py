# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RanchiLapseAdjustment(models.Model):
    """Settle an outstanding loan from the member's savings, collecting any shortfall in cash."""
    _name = 'ranchi.lapse.adjustment'
    _description = 'Ranchi Lapse Adjustment'
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
    savings_available = fields.Monetary(compute='_compute_amounts')
    loan_balance = fields.Monetary(compute='_compute_amounts')
    savings_used = fields.Monetary(compute='_compute_amounts', store=True, readonly=False, tracking=True,
                                   help="Portion of the loan settled from savings. Defaults to the maximum possible.")
    shortfall = fields.Monetary(compute='_compute_amounts', string="Cash Shortfall")
    journal_id = fields.Many2one(
        'account.journal', string="Shortfall Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Done'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False)
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    savings_transaction_id = fields.Many2one('ranchi.savings.transaction', readonly=True, copy=False)
    note = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.lapse.adjustment') or '/'
        return super().create(vals_list)

    @api.depends('member_id', 'loan_id', 'savings_used', 'state')
    def _compute_amounts(self):
        for rec in self:
            if rec.state != 'draft':
                rec.savings_available = rec.member_id.savings_available
                rec.loan_balance = 0.0
                rec.shortfall = 0.0
                continue
            rec.savings_available = rec.member_id.savings_available
            rec.loan_balance = rec.loan_id.balance
            max_from_savings = max(0.0, min(rec.savings_available, rec.loan_balance))
            if not rec.savings_used or rec.savings_used > max_from_savings:
                rec.savings_used = max_from_savings
            rec.shortfall = max(0.0, rec.loan_balance - rec.savings_used)

    @api.constrains('savings_used')
    def _check_savings_used(self):
        for rec in self:
            if rec.savings_used < 0:
                raise ValidationError(_("Savings used cannot be negative."))

    def action_confirm(self):
        Tx = self.env['ranchi.savings.transaction']
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft adjustments can be confirmed."))
            loan = rec.loan_id
            if loan.state != 'disbursed':
                raise UserError(_("Loan %s is not outstanding.", loan.name))
            currency = rec.currency_id
            company = rec.company_id
            if currency.is_zero(rec.loan_balance):
                raise UserError(_("The loan has no outstanding balance."))
            if currency.compare_amounts(rec.savings_used, rec.member_id.savings_available) > 0:
                raise UserError(_("Savings used exceeds the member's available savings."))
            if not currency.is_zero(rec.shortfall) and not rec.journal_id:
                raise UserError(_("Select the journal receiving the cash shortfall."))
            total = rec.savings_used + rec.shortfall
            allocations = loan._allocate_repayment(total)
            lines = loan._prepare_repayment_credit_lines(allocations, label=_("Lapse adjustment %s", rec.name))
            journal = rec.journal_id or company._ranchi_require('ranchi_savings_journal_id')
            liability = None
            if not currency.is_zero(rec.savings_used):
                liability = company._ranchi_require('ranchi_savings_liability_account_id')
                lines.append((0, 0, {
                    'name': _("%(ref)s settled from savings", ref=rec.name),
                    'account_id': liability.id,
                    'partner_id': rec.member_id.id,
                    'debit': rec.savings_used,
                    'credit': 0.0,
                }))
            if not currency.is_zero(rec.shortfall):
                cash_account = rec.journal_id.default_account_id
                if not cash_account:
                    raise UserError(_("Journal %s has no default account.", rec.journal_id.name))
                lines.append((0, 0, {
                    'name': _("%(ref)s cash shortfall", ref=rec.name),
                    'account_id': cash_account.id,
                    'partner_id': rec.member_id.id,
                    'debit': rec.shortfall,
                    'credit': 0.0,
                }))
            move = self.env['account.move'].create({
                'move_type': 'entry',
                'journal_id': journal.id,
                'company_id': company.id,
                'date': rec.date,
                'ref': _("Lapse adjustment %(ref)s - %(loan)s", ref=rec.name, loan=loan.name),
                'ranchi_move_type': 'lapse',
                'ranchi_loan_id': loan.id,
                'ranchi_member_id': rec.member_id.id,
                'line_ids': lines,
            })
            move.action_post()
            loan._register_repayments(move, allocations, 'lapse', lapse_id=rec.id)
            tx = Tx
            if liability:
                liability_line = move.line_ids.filtered(lambda l: l.account_id == liability)[:1]
                tx = Tx.create({
                    'member_id': rec.member_id.id,
                    'company_id': company.id,
                    'date': rec.date,
                    'amount': rec.savings_used,
                    'type': 'withdrawal',
                    'origin': 'lapse',
                    'lapse_id': rec.id,
                    'move_id': move.id,
                    'move_line_id': liability_line.id,
                    'note': _("Applied to loan %s", loan.name),
                    'state': 'posted',
                })
            rec.write({'state': 'done', 'move_id': move.id, 'savings_transaction_id': tx.id if tx else False})
            rec.message_post(body=_("Loan %(loan)s settled. Journal entry %(move)s.", loan=loan.name, move=move.name))

    def action_cancel(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft adjustments can be cancelled."))
            rec.state = 'cancelled'

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }
