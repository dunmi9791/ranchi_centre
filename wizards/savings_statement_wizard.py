# -*- coding: utf-8 -*-
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields, models


class RanchiSavingsStatementWizard(models.TransientModel):
    _name = 'ranchi.savings.statement.wizard'
    _description = 'Savings Statement'

    member_id = fields.Many2one(
        'res.partner', string="Member", required=True, domain="[('is_ranchi_member', '=', True)]")
    date_from = fields.Date(required=True, default=lambda self: date.today() - relativedelta(months=3))
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    def action_print(self):
        self.ensure_one()
        data = {'date_from': self.date_from.isoformat(), 'date_to': self.date_to.isoformat()}
        return self.env.ref('ranchi_centre.action_report_savings_statement').report_action(
            self.member_id, data=data)
