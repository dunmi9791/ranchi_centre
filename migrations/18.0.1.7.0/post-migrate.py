# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Unions created before 18.0.1.7.0 have no purse yet: give each one its purse."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ranchi.union'].with_context(active_test=False).search([])._ensure_purse()
