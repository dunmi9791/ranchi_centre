# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """The digest cron ran at the hour the digest module was installed, often during field
    collections, and slowed the officer app's sync: move it to 05:00 local time."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['digest.digest']._ranchi_schedule_digest_cron()
