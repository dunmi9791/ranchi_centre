# -*- coding: utf-8 -*-
from odoo import fields, models


class RanchiWithdrawalPayWizard(models.TransientModel):
    _name = 'ranchi.withdrawal.pay.wizard'
    _description = 'Pay Withdrawal Request'

    request_id = fields.Many2one('ranchi.withdrawal.request', required=True, readonly=True)
    company_id = fields.Many2one(related='request_id.company_id')
    currency_id = fields.Many2one(related='request_id.currency_id')
    amount = fields.Monetary(related='request_id.amount')
    fee_amount = fields.Monetary(related='request_id.fee_amount')
    net_amount = fields.Monetary(related='request_id.net_amount')
    journal_id = fields.Many2one(
        'account.journal', string="Payout Journal", required=True, check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    pay_date = fields.Date(required=True, default=fields.Date.context_today)

    def action_confirm(self):
        self.ensure_one()
        self.request_id.action_pay(journal=self.journal_id, pay_date=self.pay_date)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.withdrawal.request',
            'res_id': self.request_id.id,
            'view_mode': 'form',
        }
