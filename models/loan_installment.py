# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.osv import expression


class RanchiLoanInstallment(models.Model):
    _name = 'ranchi.loan.installment'
    _description = 'Ranchi Loan Installment'
    _order = 'loan_id, sequence, id'

    loan_id = fields.Many2one('ranchi.loan', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(required=True, default=1)
    company_id = fields.Many2one(related='loan_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    member_id = fields.Many2one(related='loan_id.member_id', store=True, index=True)
    union_id = fields.Many2one(related='loan_id.union_id', store=True, index=True)
    loan_state = fields.Selection(related='loan_id.state', store=True)
    date_due = fields.Date(string="Due Date", required=True, index=True)
    amount_principal = fields.Monetary(required=True)
    amount_service = fields.Monetary(default=0.0)
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True)
    move_line_id = fields.Many2one(
        'account.move.line', string="Receivable Line", readonly=True, copy=False, index=True,
        help="Debit line on the loans receivable account booked at disbursement.")
    amount_residual = fields.Monetary(compute='_compute_residual', store=True, string="Residual")
    amount_paid = fields.Monetary(compute='_compute_residual', store=True, string="Paid")
    state = fields.Selection(
        [('pending', 'Unpaid'), ('partial', 'Partially Paid'), ('paid', 'Paid')],
        compute='_compute_residual', store=True, index=True)
    status = fields.Selection(
        [('not_due', 'Not Yet Due'), ('due', 'Due Today'), ('overdue', 'Overdue'), ('paid', 'Paid')],
        compute='_compute_status', search='_search_status')
    days_overdue = fields.Integer(compute='_compute_status')
    repayment_ids = fields.One2many('ranchi.loan.repayment', 'installment_id')
    last_payment_date = fields.Date(compute='_compute_last_payment')

    @api.depends('loan_id.name', 'sequence')
    def _compute_display_name(self):
        for inst in self:
            inst.display_name = f"{inst.loan_id.name or ''} #{inst.sequence}"

    @api.depends('amount_principal', 'amount_service')
    def _compute_amount_total(self):
        for inst in self:
            inst.amount_total = inst.amount_principal + inst.amount_service

    @api.depends('move_line_id.amount_residual', 'move_line_id.reconciled', 'amount_total', 'loan_id.state')
    def _compute_residual(self):
        for inst in self:
            currency = inst.currency_id
            if inst.move_line_id:
                residual = inst.move_line_id.amount_residual
            else:
                residual = inst.amount_total
            if inst.loan_id.state in ('cancelled',):
                residual = 0.0
            inst.amount_residual = residual
            inst.amount_paid = inst.amount_total - residual
            if currency and currency.is_zero(residual) and inst.move_line_id:
                inst.state = 'paid'
            elif currency and not currency.is_zero(inst.amount_paid):
                inst.state = 'partial'
            else:
                inst.state = 'pending'

    @api.depends('date_due', 'state', 'loan_id.loan_type_id.grace_days')
    def _compute_status(self):
        today = fields.Date.context_today(self)
        for inst in self:
            grace = inst.loan_id.loan_type_id.grace_days or 0
            if inst.state == 'paid':
                inst.status = 'paid'
                inst.days_overdue = 0
            elif not inst.date_due or today < inst.date_due:
                inst.status = 'not_due'
                inst.days_overdue = 0
            elif (today - inst.date_due).days > grace:
                inst.status = 'overdue'
                inst.days_overdue = (today - inst.date_due).days
            else:
                inst.status = 'due'
                inst.days_overdue = 0

    def _search_status(self, operator, value):
        today = fields.Date.context_today(self)
        values = value if isinstance(value, (list, tuple)) else [value]
        if operator in ('!=', 'not in'):
            positive = self._search_status('in', values)
            return [('id', 'not in', self.search(positive).ids)]
        if operator not in ('=', 'in'):
            return expression.FALSE_DOMAIN
        domains = []
        unpaid = [('state', '!=', 'paid'), ('loan_state', '=', 'disbursed')]
        for val in values:
            if val == 'paid':
                domains.append([('state', '=', 'paid')])
            elif val == 'not_due':
                domains.append(unpaid + [('date_due', '>', today)])
            elif val == 'due':
                domains.append(unpaid + [('date_due', '=', today)])
            elif val == 'overdue':
                # grace days are ignored in search; the computed field applies them precisely
                domains.append(unpaid + [('date_due', '<', today)])
        return expression.OR(domains) if domains else expression.FALSE_DOMAIN

    @api.depends('repayment_ids.date')
    def _compute_last_payment(self):
        for inst in self:
            dates = inst.repayment_ids.mapped('date')
            inst.last_payment_date = max(dates) if dates else False
