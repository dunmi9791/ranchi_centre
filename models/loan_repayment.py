# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RanchiLoanRepayment(models.Model):
    _name = 'ranchi.loan.repayment'
    _description = 'Ranchi Loan Repayment'
    _order = 'date desc, id desc'

    loan_id = fields.Many2one('ranchi.loan', required=True, ondelete='restrict', index=True)
    installment_id = fields.Many2one('ranchi.loan.installment', ondelete='restrict', index=True)
    member_id = fields.Many2one(related='loan_id.member_id', store=True)
    union_id = fields.Many2one(related='loan_id.union_id', store=True)
    company_id = fields.Many2one(related='loan_id.company_id', store=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    amount = fields.Monetary(required=True)
    source = fields.Selection(
        [('collection', 'Field Collection'), ('lapse', 'Savings Adjustment'),
         ('api', 'Mobile App'), ('manual', 'Manual'), ('writeoff', 'Write-off')],
        required=True, default='manual')
    collection_line_id = fields.Many2one('ranchi.collection.line', ondelete='set null')
    collection_id = fields.Many2one(related='collection_line_id.collection_id', store=True)
    lapse_id = fields.Many2one('ranchi.lapse.adjustment', ondelete='set null')
    move_line_id = fields.Many2one('account.move.line', string="Journal Item", readonly=True)
    move_id = fields.Many2one(related='move_line_id.move_id', store=True)

    @api.constrains('amount')
    def _check_amount(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("A repayment amount must be positive."))
