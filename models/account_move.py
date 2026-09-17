# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    ranchi_move_type = fields.Selection(
        [('membership_fee', 'Membership Fee'),
         ('loan_fee', 'Loan Fees'),
         ('disbursement', 'Loan Disbursement'),
         ('collection', 'Field Collection'),
         ('savings', 'Savings Transaction'),
         ('lapse', 'Lapse Adjustment'),
         ('writeoff', 'Loan Write-off')],
        string="Ranchi Operation", copy=False, index=True)
    ranchi_loan_id = fields.Many2one(
        'ranchi.loan', string="Loan", copy=False, index=True, ondelete='set null')
    ranchi_member_id = fields.Many2one(
        'res.partner', string="Ranchi Member", copy=False, index=True, ondelete='set null',
        help="Member concerned by a membership fee invoice.")

    def _invoice_paid_hook(self):
        res = super()._invoice_paid_hook()
        for move in self:
            if move.ranchi_move_type == 'loan_fee' and move.ranchi_loan_id:
                move.ranchi_loan_id._on_fees_paid()
            elif move.ranchi_move_type == 'membership_fee' and move.ranchi_member_id:
                move.ranchi_member_id._on_membership_fee_paid()
        return res


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ranchi_installment_id = fields.Many2one(
        'ranchi.loan.installment', string="Loan Installment", copy=False, index=True,
        ondelete='set null')
