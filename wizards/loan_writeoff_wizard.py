# -*- coding: utf-8 -*-
from odoo import fields, models


class RanchiLoanWriteoffWizard(models.TransientModel):
    _name = 'ranchi.loan.writeoff.wizard'
    _description = 'Write Off Loan'

    loan_id = fields.Many2one('ranchi.loan', required=True, readonly=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    balance = fields.Monetary(related='loan_id.balance')
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.loan_id._write_off(self.reason)
        return {'type': 'ir.actions.act_window_close'}
