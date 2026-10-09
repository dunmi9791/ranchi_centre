# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RanchiLoanType(models.Model):
    _name = 'ranchi.loan.type'
    _description = 'Ranchi Loan Type'
    _order = 'sequence, name'
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    code = fields.Char(size=16)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="Branch",
        help="Leave empty to make this loan type available to every branch.")
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_currency_id')
    description = fields.Text()

    service_rate = fields.Float(
        string="Service Charge (%)", required=True, default=0.0, digits=(5, 2),
        help="Flat percentage of the principal charged once over the life of the loan.")
    service_collection = fields.Selection(
        [('spread', 'Spread over installments'), ('upfront', 'Collected with the fees, before disbursement')],
        string="Service Charge Collection", default='spread', required=True)
    admin_charge = fields.Monetary(
        string="Administration Fee", currency_field='currency_id', default=0.0,
        help="Flat fee invoiced before disbursement.")
    risk_premium_rate = fields.Float(
        string="Risk Premium (%)", default=0.0, digits=(5, 2),
        help="Percentage of the approved amount invoiced before disbursement.")

    installment_count = fields.Integer(string="Number of Installments", required=True, default=20)
    installment_period = fields.Selection(
        [('daily', 'Daily'), ('weekly', 'Weekly'), ('biweekly', 'Every two weeks'), ('monthly', 'Monthly')],
        required=True, default='weekly',
        help="Installments that fall on a public holiday move: weekly and two-weekly ones by one "
             "week (to the next union day), daily and monthly ones to the next working day. "
             "Daily installments also skip weekends. For daily, weekly and two-weekly loans the "
             "later installments move along with it.")
    meeting_frequency = fields.Selection(
        [('weekly', 'Weekly, on the union day'), ('daily', 'Daily, no union day')],
        string="Union Meetings", required=True, default='weekly',
        help="How unions running this loan type meet. Daily unions (e.g. Rapid) have no union day: "
             "the officer visits them every working day (weekends and public holidays are skipped).")
    manager_ids = fields.Many2many(
        'res.users', 'ranchi_loan_type_manager_rel', 'loan_type_id', 'user_id', string="Field Managers",
        domain=lambda self: [('groups_id', 'in', self.env.ref('ranchi_centre.group_ranchi_manager').id)],
        help="Loan product managers in charge of this product's field collections. They only see "
             "unions, members, loans and collections of the loan types that list them here.")
    grace_days = fields.Integer(
        string="Grace Days", default=0,
        help="Days after the due date before an installment is treated as overdue.")
    min_amount = fields.Monetary(currency_field='currency_id', default=0.0)
    max_amount = fields.Monetary(currency_field='currency_id', default=0.0,
                                 help="0 means no ceiling at loan type level.")
    stage_ids = fields.One2many('ranchi.loan.stage', 'loan_type_id', string="Stages")
    loan_count = fields.Integer(compute='_compute_loan_count')
    union_ids = fields.One2many('ranchi.union', 'loan_type_id', string="Unions")
    union_count = fields.Integer(compute='_compute_union_count')

    @api.depends('company_id')
    def _compute_currency_id(self):
        for rec in self:
            rec.currency_id = (rec.company_id or self.env.company).currency_id

    def _compute_loan_count(self):
        groups = self.env['ranchi.loan']._read_group(
            [('loan_type_id', 'in', self.ids)], ['loan_type_id'], ['__count'])
        counts = {lt.id: count for lt, count in groups}
        for rec in self:
            rec.loan_count = counts.get(rec.id, 0)

    @api.depends('union_ids')
    def _compute_union_count(self):
        for rec in self:
            rec.union_count = len(rec.union_ids)

    def write(self, vals):
        if 'meeting_frequency' in vals:
            for rec in self:
                if rec.meeting_frequency == vals['meeting_frequency']:
                    continue
                unions = rec.union_ids.filtered(lambda u: u.state != 'closed')
                if unions:
                    raise UserError(_(
                        "Cannot change how %(type)s unions meet while %(count)s union(s) run it: "
                        "%(unions)s. Their union days would no longer be valid.",
                        type=rec.name, count=len(unions), unions=', '.join(unions.mapped('name'))))
        return super().write(vals)

    def action_view_unions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_union')
        action['domain'] = [('loan_type_id', '=', self.id)]
        action['context'] = {'default_loan_type_id': self.id, 'search_default_active_state': 1}
        return action

    @api.constrains('installment_count', 'service_rate', 'risk_premium_rate', 'min_amount', 'max_amount')
    def _check_values(self):
        for rec in self:
            if rec.installment_count <= 0:
                raise ValidationError(_("The number of installments must be greater than zero."))
            if rec.service_rate < 0 or rec.risk_premium_rate < 0:
                raise ValidationError(_("Rates cannot be negative."))
            if rec.max_amount and rec.min_amount > rec.max_amount:
                raise ValidationError(_("The minimum amount cannot exceed the maximum amount."))

    def _period_delta(self):
        """Return a relativedelta for one installment period."""
        self.ensure_one()
        return {
            'daily': relativedelta(days=1),
            'weekly': relativedelta(weeks=1),
            'biweekly': relativedelta(weeks=2),
            'monthly': relativedelta(months=1),
        }[self.installment_period]

    # ---- public holidays -------------------------------------------------

    @api.model
    def _ranchi_holiday_dates(self, company, date_from, date_to):
        """Return the set of dates between *date_from* and *date_to* (inclusive) covered by a
        public holiday of *company*. Public holidays are calendar leaves without a resource."""
        calendar = company.resource_calendar_id
        tz = pytz.timezone(calendar.tz or company.partner_id.tz or 'UTC')
        start = tz.localize(datetime.combine(date_from, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        stop = tz.localize(datetime.combine(date_to, time.max)).astimezone(pytz.utc).replace(tzinfo=None)
        leaves = self.env['resource.calendar.leaves'].sudo().search([
            ('resource_id', '=', False),
            ('time_type', '=', 'leave'),
            ('company_id', 'in', [company.id, False]),
            ('calendar_id', 'in', [calendar.id, False]),
            ('date_from', '<=', stop),
            ('date_to', '>=', start),
        ])
        days = set()
        for leave in leaves:
            day = pytz.utc.localize(leave.date_from).astimezone(tz).date()
            last = pytz.utc.localize(leave.date_to).astimezone(tz).date()
            while day <= last:
                if date_from <= day <= date_to:
                    days.add(day)
                day += timedelta(days=1)
        return days

    def _holiday_shift(self):
        """Step used to move an installment off a non-working day: union-day loans move to
        the next meeting a week later, the others to the next day."""
        self.ensure_one()
        if self.installment_period in ('weekly', 'biweekly'):
            return relativedelta(weeks=1)
        return relativedelta(days=1)

    def _is_non_working(self, day, holidays):
        self.ensure_one()
        if day in holidays:
            return True
        return self.installment_period == 'daily' and day.weekday() >= 5

    def _due_dates(self, start, count, company):
        """Return *count* due dates starting at *start*, moved off public holidays (and
        weekends for daily loans). A moved installment pushes the later ones along with it,
        except for monthly loans, which keep their day of the month."""
        self.ensure_one()
        if count <= 0:
            return []
        period = self._period_delta()
        shift = self._holiday_shift()
        window_end = start + period * count + relativedelta(months=2)
        holidays = self._ranchi_holiday_dates(company, start, window_end)

        def next_working(day):
            nonlocal window_end, holidays
            for _i in range(400):
                if day > window_end:
                    window_end = day + relativedelta(months=6)
                    holidays = self._ranchi_holiday_dates(company, start, window_end)
                if not self._is_non_working(day, holidays):
                    return day
                day += shift
            raise UserError(_("Could not find a working day for the %s schedule. Check the public holidays.", self.name))

        dates = []
        if self.installment_period == 'monthly':
            for k in range(count):
                day = start + relativedelta(months=k)
                if dates and day <= dates[-1]:
                    day = dates[-1] + timedelta(days=1)
                dates.append(next_working(day))
        else:
            day = start
            for _k in range(count):
                day = next_working(day)
                dates.append(day)
                day += period
        return dates


class RanchiLoanStage(models.Model):
    _name = 'ranchi.loan.stage'
    _description = 'Ranchi Loan Stage (cycle ladder)'
    _order = 'loan_type_id, sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    loan_type_id = fields.Many2one('ranchi.loan.type', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='loan_type_id.currency_id')
    max_principal = fields.Monetary(
        string="Maximum Principal", currency_field='currency_id', required=True)
    min_cycle = fields.Integer(
        string="Loans Repaid Required", default=0,
        help="How many loans the member must have fully repaid to be eligible for this stage.")

    @api.constrains('max_principal', 'min_cycle')
    def _check_values(self):
        for rec in self:
            if rec.max_principal <= 0:
                raise ValidationError(_("The maximum principal must be positive."))
            if rec.min_cycle < 0:
                raise ValidationError(_("The required loan cycle cannot be negative."))
