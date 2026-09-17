# -*- coding: utf-8 -*-
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields, models, _
from odoo.exceptions import UserError


class RanchiInterestGenerateWizard(models.TransientModel):
    _name = 'ranchi.interest.generate.wizard'
    _description = 'Generate Savings Interest'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    period_end = fields.Date(
        string="Month Ending", required=True,
        default=lambda self: date.today().replace(day=1) - relativedelta(days=1),
        help="Interest is computed on the balance at this date for the month ending on it.")

    def action_generate(self):
        self.ensure_one()
        created = self.env['ranchi.savings.rate'].accrue_interest(self.company_id, self.period_end)
        if not created:
            raise UserError(_(
                "No interest was generated. Check that an active rate exists for %s and that members "
                "do not already have interest for this month.", self.period_end))
        return {
            'name': _('Interest Transactions'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.savings.transaction',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids)],
        }
