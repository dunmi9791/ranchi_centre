# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

MOVE_TYPES = ('deposit', 'withdrawal', 'interest', 'fee')


class RanchiSavingsTransaction(models.Model):
    _name = 'ranchi.savings.transaction'
    _description = 'Ranchi Savings Transaction'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string="Reference", readonly=True, copy=False, default=lambda self: _('New'))
    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, index=True, tracking=True, check_company=True,
        domain="[('is_ranchi_member', '=', True)]")
    union_id = fields.Many2one(related='member_id.union_id', store=True, readonly=True, index=True)
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True, index=True)
    amount = fields.Monetary(required=True, tracking=True)
    type = fields.Selection(
        [('deposit', 'Deposit'), ('withdrawal', 'Withdrawal'), ('interest', 'Interest'),
         ('fee', 'Fee'), ('hold', 'Hold')],
        required=True, default='deposit', tracking=True, index=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True, copy=False, index=True)
    origin = fields.Selection(
        [('manual', 'Manual'), ('collection', 'Field Collection'), ('withdrawal', 'Withdrawal Request'),
         ('interest', 'Interest Run'), ('lapse', 'Lapse Adjustment'), ('api', 'Mobile App')],
        default='manual', required=True, readonly=True)
    journal_id = fields.Many2one(
        'account.journal', check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Cash or bank journal the money went through. Required for deposits and withdrawals.")
    signed_amount = fields.Monetary(compute='_compute_signed_amount', store=True,
                                    help="Effect on the member's savings balance.")
    note = fields.Char()
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    move_line_id = fields.Many2one(
        'account.move.line', string="Liability Line", readonly=True, copy=False,
        help="The savings liability line that carries this transaction in accounting.")
    collection_line_id = fields.Many2one('ranchi.collection.line', readonly=True, ondelete='set null')
    withdrawal_request_id = fields.Many2one('ranchi.withdrawal.request', readonly=True, ondelete='set null')
    lapse_id = fields.Many2one('ranchi.lapse.adjustment', readonly=True, ondelete='set null')
    balance_after = fields.Monetary(compute='_compute_balance_after')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.savings.transaction') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(tx.state == 'posted' and tx.type != 'hold' for tx in self):
            raise UserError(_("Posted savings transactions cannot be deleted. Cancel them instead."))

    @api.depends('type', 'amount', 'state')
    def _compute_signed_amount(self):
        for tx in self:
            if tx.state != 'posted' or tx.type == 'hold':
                tx.signed_amount = 0.0
            elif tx.type in ('deposit', 'interest'):
                tx.signed_amount = tx.amount
            else:
                tx.signed_amount = -tx.amount

    def _compute_balance_after(self):
        for tx in self:
            if tx.state != 'posted' or tx.type == 'hold':
                tx.balance_after = 0.0
                continue
            later = self.search([
                ('member_id', '=', tx.member_id.id), ('state', '=', 'posted'), ('type', '!=', 'hold'),
                '|', ('date', '>', tx.date), '&', ('date', '=', tx.date), ('id', '>', tx.id)])
            tx.balance_after = tx.member_id.savings_balance - sum(later.mapped('signed_amount'))

    @api.constrains('amount')
    def _check_amount(self):
        for tx in self:
            if tx.amount <= 0:
                raise ValidationError(_("The amount must be positive."))

    @api.constrains('type', 'journal_id', 'state')
    def _check_journal(self):
        for tx in self:
            if tx.state == 'posted' and tx.type in ('deposit', 'withdrawal') and not tx.journal_id \
                    and not tx.collection_line_id and not tx.lapse_id:
                raise ValidationError(_("A cash or bank journal is required for %s transactions.", tx.type))

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------
    def action_post(self):
        for tx in self:
            if tx.state != 'draft':
                raise UserError(_("Only draft transactions can be posted."))
            if tx.type == 'hold':
                tx.state = 'posted'
                continue
            if tx.type == 'withdrawal':
                available = tx.member_id.savings_available + tx._held_for_own_request()
                if tx.currency_id.compare_amounts(tx.amount, available) > 0:
                    raise UserError(_(
                        "%(member)s only has %(available)s available in savings.",
                        member=tx.member_id.display_name, available=available))
            # Journals, accounts and the journal entry need accounting rights that credit
            # officers (deposits from the field app) do not have: post as superuser.
            tx.sudo()._create_move()
            tx.state = 'posted'

    def _held_for_own_request(self):
        """When paying a withdrawal request, its own hold must not block the payout."""
        self.ensure_one()
        req = self.withdrawal_request_id
        hold = req.hold_transaction_id if req else self.env['ranchi.savings.transaction']
        return hold.amount if hold and hold.state == 'posted' else 0.0

    def _get_accounts(self):
        self.ensure_one()
        company = self.company_id
        liability = company._ranchi_require('ranchi_savings_liability_account_id')
        if self.type in ('deposit', 'withdrawal'):
            journal = self.journal_id or company._ranchi_require('ranchi_collection_journal_id')
            counterpart = journal.default_account_id
            if not counterpart:
                raise UserError(_("Journal %s has no default account.", journal.name))
        elif self.type == 'interest':
            journal = company._ranchi_require('ranchi_savings_journal_id')
            counterpart = company._ranchi_require('ranchi_interest_expense_account_id')
        else:  # fee
            journal = company._ranchi_require('ranchi_savings_journal_id')
            counterpart = company._ranchi_require('ranchi_fee_income_account_id')
        return journal, liability, counterpart

    def _create_move(self):
        self.ensure_one()
        journal, liability, counterpart = self._get_accounts()
        partner = self.member_id
        label = f"{self.name} - {dict(self._fields['type'].selection)[self.type]}"
        if self.note:
            label = f"{label} - {self.note}"
        credit_liability = self.type in ('deposit', 'interest')
        liability_line = {
            'name': label,
            'account_id': liability.id,
            'partner_id': partner.id,
            'debit': 0.0 if credit_liability else self.amount,
            'credit': self.amount if credit_liability else 0.0,
        }
        counterpart_line = {
            'name': label,
            'account_id': counterpart.id,
            'partner_id': partner.id if self.type in ('deposit', 'withdrawal') else False,
            'debit': self.amount if credit_liability else 0.0,
            'credit': 0.0 if credit_liability else self.amount,
        }
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'company_id': self.company_id.id,
            'date': self.date,
            'ref': label,
            'ranchi_move_type': 'savings',
            'ranchi_member_id': partner.id,
            'line_ids': [(0, 0, liability_line), (0, 0, counterpart_line)],
        })
        move.action_post()
        self.write({
            'move_id': move.id,
            'move_line_id': move.line_ids.filtered(lambda l: l.account_id == liability)[:1].id,
        })

    def action_cancel(self):
        for tx in self:
            if tx.state == 'cancelled':
                continue
            if tx.origin not in ('manual', 'api', 'interest') and tx.type != 'hold':
                raise UserError(_(
                    "%s was created by another document; cancel that document instead.", tx.name))
            if tx.move_id and tx.move_id.state == 'posted':
                if any(l.reconciled for l in tx.move_id.line_ids):
                    raise UserError(_("The journal entry of %s is reconciled and cannot be reversed.", tx.name))
                reversal = tx.move_id._reverse_moves(
                    [{'ref': _("Reversal of %s", tx.name), 'date': fields.Date.context_today(self)}],
                    cancel=True)
                tx.message_post(body=_("Reversed by %s", reversal.name))
            tx.state = 'cancelled'

    def action_reset_to_draft(self):
        for tx in self:
            if tx.state != 'cancelled' or tx.move_id:
                raise UserError(_("Only cancelled transactions without accounting can be reset."))
            tx.state = 'draft'

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
        }
