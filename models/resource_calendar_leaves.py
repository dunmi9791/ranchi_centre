# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class ResourceCalendarLeaves(models.Model):
    _inherit = 'resource.calendar.leaves'

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        leaves._ranchi_reschedule_loans()
        return leaves

    def write(self, vals):
        res = super().write(vals)
        if {'date_from', 'date_to', 'resource_id', 'company_id', 'calendar_id', 'time_type'} & set(vals):
            self._ranchi_reschedule_loans()
        return res

    def _ranchi_reschedule_loans(self):
        """Move the upcoming installments that fall on a new or changed public holiday.
        Removing a holiday does not move installments back."""
        holidays = self.filtered(lambda l: not l.resource_id and l.time_type == 'leave')
        if not holidays:
            return
        Loan = self.env['ranchi.loan'].sudo()
        today = fields.Date.context_today(self)
        loans = Loan.browse()
        for leave in holidays:
            # Pad by a day on each side: the stored datetimes are in UTC.
            date_from = max(leave.date_from.date() - timedelta(days=1), today)
            date_to = leave.date_to.date() + timedelta(days=1)
            if date_to < today:
                continue
            domain = [
                ('state', 'in', ('approved', 'fees', 'disbursed')),
                ('installment_ids.date_due', '>=', date_from),
                ('installment_ids.date_due', '<=', date_to),
            ]
            if leave.company_id:
                domain.append(('company_id', '=', leave.company_id.id))
            loans |= Loan.search(domain)
        loans._reschedule_for_holidays()
