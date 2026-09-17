# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ReportSavingsStatement(models.AbstractModel):
    _name = 'report.ranchi_centre.report_savings_statement'
    _description = 'Member Savings Statement'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        members = self.env['res.partner'].browse(docids)
        date_from = fields.Date.from_string(data.get('date_from')) if data.get('date_from') else False
        date_to = fields.Date.from_string(data.get('date_to')) if data.get('date_to') else fields.Date.context_today(self)
        Tx = self.env['ranchi.savings.transaction']
        statements = {}
        for member in members:
            base = [('member_id', '=', member.id), ('state', '=', 'posted'), ('type', '!=', 'hold')]
            opening = 0.0
            if date_from:
                opening = sum(Tx.search(base + [('date', '<', date_from)]).mapped('signed_amount'))
            domain = base + [('date', '<=', date_to)]
            if date_from:
                domain.append(('date', '>=', date_from))
            lines = []
            running = opening
            for tx in Tx.search(domain, order='date, id'):
                running += tx.signed_amount
                lines.append({'tx': tx, 'running': running})
            statements[member.id] = {'opening': opening, 'closing': running, 'lines': lines}
        return {
            'doc_ids': docids,
            'doc_model': 'res.partner',
            'docs': members,
            'date_from': date_from,
            'date_to': date_to,
            'statements': statements,
        }
