# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

MANAGER_GROUP = 'ranchi_centre.group_ranchi_manager'
GENERAL_MANAGER_GROUP = 'ranchi_centre.group_ranchi_general_manager'


class RanchiUnionPurse(models.Model):
    """Money the branch holds for a union. Created with the union; the balance is the sum of its
    posted lines, and the money itself sits on the branch's Union Purse liability account."""
    _name = 'ranchi.union.purse'
    _description = 'Ranchi Union Purse'
    _inherit = ['mail.thread']
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    union_id = fields.Many2one(
        'ranchi.union', string="Union", required=True, ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one(related='union_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='union_id.currency_id')
    loan_type_id = fields.Many2one(related='union_id.loan_type_id', store=True)
    credit_officer_id = fields.Many2one(related='union_id.credit_officer_id')
    credit_officer_user_id = fields.Many2one(related='union_id.credit_officer_user_id', store=True)
    line_ids = fields.One2many('ranchi.union.purse.line', 'purse_id', string="Entries")
    balance = fields.Monetary(
        compute='_compute_balance', store=True, currency_field='currency_id',
        help="Union purse fees collected plus manual credits, less manual debits.")
    total_credit = fields.Monetary(compute='_compute_balance', store=True, currency_field='currency_id',
                                   string="Total In")
    total_debit = fields.Monetary(compute='_compute_balance', store=True, currency_field='currency_id',
                                  string="Total Out")

    _sql_constraints = [
        ('union_uniq', 'unique(union_id)', 'A union has exactly one purse.'),
    ]

    @api.depends('union_id.name')
    def _compute_name(self):
        for purse in self:
            purse.name = _("Purse - %s", purse.union_id.name) if purse.union_id else False

    @api.depends('line_ids.state', 'line_ids.direction', 'line_ids.amount')
    def _compute_balance(self):
        for purse in self:
            posted = purse.line_ids.filtered(lambda l: l.state == 'posted')
            credit = sum(posted.filtered(lambda l: l.direction == 'credit').mapped('amount'))
            debit = sum(posted.filtered(lambda l: l.direction == 'debit').mapped('amount'))
            purse.total_credit = credit
            purse.total_debit = debit
            purse.balance = credit - debit

    def action_new_entry(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Purse Entry"),
            'res_model': 'ranchi.union.purse.line',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_purse_id': self.id, 'default_origin': 'manual'},
        }

    def action_view_union(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.union',
            'res_id': self.union_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class RanchiUnionPurseLine(models.Model):
    _name = 'ranchi.union.purse.line'
    _description = 'Ranchi Union Purse Entry'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    # fields that may no longer change once an entry has left draft
    _LOCKED_FIELDS = {'purse_id', 'date', 'direction', 'amount', 'journal_id', 'origin', 'loan_id', 'move_id'}

    purse_id = fields.Many2one(
        'ranchi.union.purse', string="Purse", required=True, ondelete='cascade', index=True)
    union_id = fields.Many2one(related='purse_id.union_id', store=True, index=True)
    company_id = fields.Many2one(related='purse_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='purse_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    name = fields.Char(string="Reason", required=True, tracking=True)
    origin = fields.Selection(
        [('fee', 'Union Purse Fee'), ('manual', 'Manager Entry'), ('reversal', 'Fee Reversal')],
        required=True, default='manual', readonly=True)
    direction = fields.Selection(
        [('credit', 'Money In'), ('debit', 'Money Out')], required=True, default='credit', tracking=True)
    amount = fields.Monetary(required=True, currency_field='currency_id', tracking=True)
    signed_amount = fields.Monetary(compute='_compute_signed_amount', store=True, currency_field='currency_id')
    loan_id = fields.Many2one('ranchi.loan', string="Loan", readonly=True, index=True, ondelete='set null')
    member_id = fields.Many2one(related='loan_id.member_id', string="Member")
    journal_id = fields.Many2one(
        'account.journal', string="Cash/Bank Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        help="Where the cash comes in from or goes out to for a manager entry.")
    move_id = fields.Many2one('account.move', string="Journal Entry", readonly=True, copy=False)
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        default='draft', required=True, readonly=True, copy=False, tracking=True, index=True)
    posted_by_id = fields.Many2one('res.users', string="Posted By", readonly=True, copy=False)

    @api.depends('direction', 'amount')
    def _compute_signed_amount(self):
        for line in self:
            line.signed_amount = line.amount if line.direction == 'credit' else -line.amount

    @api.constrains('amount')
    def _check_amount(self):
        for line in self:
            if line.currency_id.compare_amounts(line.amount, 0.0) <= 0:
                raise ValidationError(_("A purse entry must have a positive amount."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get('origin', 'manual') != 'manual' for vals in vals_list):
            raise AccessError(_("Union purse fee entries are created automatically when fees are collected."))
        return super().create(vals_list)

    def write(self, vals):
        locked = self._LOCKED_FIELDS & set(vals)
        if locked and any(line.state != 'draft' for line in self):
            raise UserError(_("Posted or cancelled purse entries cannot be changed."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(line.state != 'draft' for line in self):
            raise UserError(_("Only draft purse entries can be deleted."))

    # ------------------------------------------------------------------
    # Manager entries
    # ------------------------------------------------------------------
    def _check_manager(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only a manager can post or cancel union purse entries."))
        if user.has_group(GENERAL_MANAGER_GROUP):
            return
        for line in self:
            loan_type = line.sudo().union_id.loan_type_id
            if user not in loan_type.manager_ids:
                raise AccessError(_(
                    "%(union)s runs %(type)s; only that product's managers can move its purse.",
                    union=line.sudo().union_id.name, type=loan_type.name))

    def action_post(self):
        self._check_manager()
        for line in self:
            if line.state != 'draft':
                raise UserError(_("Only draft purse entries can be posted."))
            if line.origin != 'manual':
                raise UserError(_("Only manager entries are posted by hand."))
            if not line.journal_id:
                raise UserError(_("Choose the cash or bank journal for %s.", line.name))
            if line.direction == 'debit':
                line._check_enough_balance(line.amount)
            move = line.sudo()._create_move()
            line.write({'state': 'posted', 'move_id': move.id, 'posted_by_id': self.env.uid})
            line.purse_id.sudo().message_post(body=_(
                "%(dir)s of %(amount)s posted: %(reason)s",
                dir=dict(line._fields['direction'].selection)[line.direction],
                amount=line.currency_id.format(line.amount), reason=line.name))
        return True

    def action_cancel(self):
        """Draft entries are simply cancelled; posted manager entries are reversed in accounting."""
        self._check_manager()
        for line in self:
            if line.state == 'cancelled':
                continue
            if line.state == 'posted':
                if line.origin != 'manual':
                    raise UserError(_(
                        "%s comes from a loan's fees; cancel the loan to take it back out of the purse.",
                        line.name))
                if line.direction == 'credit':
                    line._check_enough_balance(line.amount)
                if line.move_id:
                    line.move_id.sudo()._reverse_moves(
                        [{'ref': _("Reversal of %s", line.move_id.ref or line.name),
                          'date': fields.Date.context_today(line)}], cancel=True)
            line.write({'state': 'cancelled'})
        return True

    def _check_enough_balance(self, amount):
        self.ensure_one()
        purse = self.purse_id.sudo()
        if self.currency_id.compare_amounts(amount, purse.balance) > 0:
            raise UserError(_(
                "%(purse)s holds %(balance)s; it cannot pay out %(amount)s.",
                purse=purse.name, balance=self.currency_id.format(purse.balance),
                amount=self.currency_id.format(amount)))

    def _create_move(self):
        self.ensure_one()
        company = self.company_id
        purse_account = company._ranchi_require('ranchi_union_purse_account_id')
        cash_account = self.journal_id.default_account_id
        if not cash_account:
            raise UserError(_("Journal %s has no default account.", self.journal_id.display_name))
        label = _("%(purse)s: %(reason)s", purse=self.purse_id.name, reason=self.name)
        money_in = self.direction == 'credit'
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'company_id': company.id,
            'date': self.date,
            'ref': label,
            'ranchi_move_type': 'purse',
            'line_ids': [
                (0, 0, {'name': label, 'account_id': purse_account.id,
                        'debit': 0.0 if money_in else self.amount,
                        'credit': self.amount if money_in else 0.0}),
                (0, 0, {'name': label, 'account_id': cash_account.id,
                        'debit': self.amount if money_in else 0.0,
                        'credit': 0.0 if money_in else self.amount}),
            ],
        })
        move.action_post()
        return move
