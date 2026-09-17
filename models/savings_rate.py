# -*- coding: utf-8 -*-
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RanchiSavingsRate(models.Model):
    _name = 'ranchi.savings.rate'
    _description = 'Ranchi Savings Interest Rate'
    _order = 'active_from desc, id desc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="Branch", help="Leave empty to apply to every branch.")
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    rate_type = fields.Selection(
        [('flat', 'Flat'), ('tiered', 'Tiered by balance')], default='flat', required=True)
    annual_rate = fields.Float(string="Annual Rate (%)", digits=(5, 2))
    tier_ids = fields.One2many('ranchi.savings.rate.tier', 'rate_id', string="Tiers")
    active_from = fields.Date(required=True, default=fields.Date.context_today)
    active_to = fields.Date()
    min_balance = fields.Monetary(
        string="Minimum Balance to Earn Interest", currency_field='currency_id', default=0.0)

    @api.depends('company_id')
    def _compute_currency_id(self):
        for rate in self:
            rate.currency_id = (rate.company_id or self.env.company).currency_id

    @api.constrains('annual_rate', 'active_from', 'active_to', 'rate_type', 'tier_ids')
    def _check_values(self):
        for rate in self:
            if rate.annual_rate < 0:
                raise ValidationError(_("The annual rate cannot be negative."))
            if rate.active_to and rate.active_to < rate.active_from:
                raise ValidationError(_("'Active To' must be after 'Active From'."))
            if rate.rate_type == 'tiered' and not rate.tier_ids:
                raise ValidationError(_("A tiered rate needs at least one tier."))

    @api.model
    def _get_rate(self, company, on_date):
        return self.search([
            ('active_from', '<=', on_date),
            '|', ('active_to', '=', False), ('active_to', '>=', on_date),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], order='company_id desc, active_from desc', limit=1)

    def _monthly_interest(self, balance):
        """Interest for one month on *balance*."""
        self.ensure_one()
        if balance <= 0 or balance < self.min_balance:
            return 0.0
        if self.rate_type == 'flat':
            return balance * (self.annual_rate / 100.0) / 12.0
        interest = 0.0
        for tier in self.tier_ids.sorted('amount_from'):
            upper = tier.amount_to if tier.amount_to else float('inf')
            slice_amount = max(0.0, min(balance, upper) - tier.amount_from)
            interest += slice_amount * (tier.annual_rate / 100.0) / 12.0
        return interest

    @api.model
    def _cron_accrue_interest(self):
        """Accrue last month's interest for every company that has a rate."""
        today = date.today()
        period_end = today.replace(day=1) - relativedelta(days=1)
        for company in self.env['res.company'].search([]):
            self.accrue_interest(company, period_end)

    @api.model
    def accrue_interest(self, company, period_end):
        """Post one interest transaction per member for the month ending *period_end*.

        Idempotent: members that already have an interest transaction dated
        *period_end* are skipped.
        """
        rate = self._get_rate(company, period_end)
        if not rate or not company.ranchi_savings_liability_account_id:
            return self.env['ranchi.savings.transaction']
        Tx = self.env['ranchi.savings.transaction'].with_company(company)
        members = self.env['res.partner'].with_company(company).search([
            ('is_ranchi_member', '=', True), ('membership_state', '=', 'confirmed'),
            '|', ('company_id', '=', False), ('company_id', '=', company.id)])
        already = Tx.search([
            ('type', '=', 'interest'), ('date', '=', period_end), ('company_id', '=', company.id),
            ('member_id', 'in', members.ids), ('state', '!=', 'cancelled')]).mapped('member_id')
        created = Tx
        for member in members - already:
            interest = company.currency_id.round(rate._monthly_interest(member.savings_balance))
            if company.currency_id.is_zero(interest):
                continue
            tx = Tx.create({
                'member_id': member.id,
                'company_id': company.id,
                'date': period_end,
                'amount': interest,
                'type': 'interest',
                'origin': 'interest',
                'note': _("Interest %(month)s (%(rate)s)", month=period_end.strftime('%B %Y'), rate=rate.name),
            })
            tx.action_post()
            created |= tx
        return created


class RanchiSavingsRateTier(models.Model):
    _name = 'ranchi.savings.rate.tier'
    _description = 'Ranchi Savings Rate Tier'
    _order = 'amount_from, id'

    rate_id = fields.Many2one('ranchi.savings.rate', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='rate_id.currency_id')
    amount_from = fields.Monetary(required=True, default=0.0)
    amount_to = fields.Monetary(help="0 means no upper limit.")
    annual_rate = fields.Float(string="Annual Rate (%)", digits=(5, 2), required=True)

    @api.constrains('amount_from', 'amount_to', 'annual_rate')
    def _check_values(self):
        for tier in self:
            if tier.amount_to and tier.amount_to <= tier.amount_from:
                raise ValidationError(_("A tier's upper bound must exceed its lower bound."))
            if tier.annual_rate < 0:
                raise ValidationError(_("Rates cannot be negative."))
