# -*- coding: utf-8 -*-
from odoo import fields, models


class RanchiSavingsDepositWizard(models.TransientModel):
    _name = 'ranchi.savings.deposit.wizard'
    _description = 'Record Savings Deposit'

    member_id = fields.Many2one(
        'res.partner', string="Member", required=True,
        domain="[('is_ranchi_member', '=', True), ('membership_state', '=', 'confirmed')]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    amount = fields.Monetary(required=True)
    journal_id = fields.Many2one(
        'account.journal', required=True, check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)
    note = fields.Char()

    def action_confirm(self):
        self.ensure_one()
        tx = self.env['ranchi.savings.transaction'].create({
            'member_id': self.member_id.id,
            'company_id': self.company_id.id,
            'date': self.date,
            'amount': self.amount,
            'type': 'deposit',
            'origin': 'manual',
            'journal_id': self.journal_id.id,
            'note': self.note,
        })
        tx.action_post()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.savings.transaction',
            'res_id': tx.id,
            'view_mode': 'form',
        }
