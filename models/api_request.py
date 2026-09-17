# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields, models


class RanchiApiRequest(models.Model):
    """Idempotency log for the JSON API: same key + same user returns the stored response."""
    _name = 'ranchi.api.request'
    _description = 'Ranchi API Request (idempotency)'
    _order = 'id desc'

    key = fields.Char(string="Idempotency Key", required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='cascade')
    route = fields.Char(required=True)
    response = fields.Text()

    _sql_constraints = [
        ('key_user_uniq', 'unique(key, user_id)', 'Duplicate idempotency key for this user.'),
    ]

    def _cron_purge(self, days=30):
        cutoff = fields.Datetime.now() - timedelta(days=days)
        self.search([('create_date', '<', cutoff)]).unlink()
