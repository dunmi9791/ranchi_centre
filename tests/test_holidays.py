# -*- coding: utf-8 -*-
from datetime import date, datetime, time, timedelta

import pytz

from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestHolidays(RanchiCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.daily_type = cls.env['ranchi.loan.type'].create({
            'name': 'Rapid Daily', 'installment_count': 5, 'installment_period': 'daily',
            'meeting_frequency': 'daily'})
        cls.monthly_type = cls.env['ranchi.loan.type'].create({
            'name': 'Monthly', 'installment_count': 3, 'installment_period': 'monthly'})

    def _holiday(self, day, company=None):
        company = company or self.company
        tz = pytz.timezone(company.resource_calendar_id.tz or 'UTC')

        def to_utc(moment):
            return tz.localize(moment).astimezone(pytz.utc).replace(tzinfo=None)
        return self.env['resource.calendar.leaves'].create({
            'name': 'Holiday', 'company_id': company.id, 'calendar_id': False,
            'date_from': to_utc(datetime.combine(day, time.min)),
            'date_to': to_utc(datetime.combine(day, time(23, 59, 59))),
        })

    def test_weekly_holiday_pushes_later_installments(self):
        self._holiday(date(2030, 1, 21))
        loan = self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0,
            'date_first_due': date(2030, 1, 7)})
        loan.action_apply()
        loan.action_approve()
        self.assertEqual(
            loan.installment_ids.sorted('sequence').mapped('date_due'),
            [date(2030, 1, 7), date(2030, 1, 14), date(2030, 1, 28), date(2030, 2, 4)])

    def test_daily_skips_weekends_and_holidays(self):
        self._holiday(date(2030, 1, 16))  # Wednesday
        dates = self.daily_type._due_dates(date(2030, 1, 11), 5, self.company)  # Friday
        self.assertEqual(dates, [date(2030, 1, 11), date(2030, 1, 14), date(2030, 1, 15),
                                 date(2030, 1, 17), date(2030, 1, 18)])
        self.assertFalse([d for d in dates if d.weekday() >= 5])

    def test_monthly_moves_one_day_without_drift(self):
        self._holiday(date(2030, 4, 5))
        dates = self.monthly_type._due_dates(date(2030, 3, 5), 3, self.company)
        self.assertEqual(dates, [date(2030, 3, 5), date(2030, 4, 6), date(2030, 5, 5)])

    def test_other_company_holiday_is_ignored(self):
        other = self.env['res.company'].create({'name': 'Other Branch'})
        self._holiday(date(2030, 1, 21), company=other)
        dates = self.loan_type._due_dates(date(2030, 1, 7), 4, self.company)
        self.assertEqual(dates, [date(2030, 1, 7), date(2030, 1, 14), date(2030, 1, 21), date(2030, 1, 28)])

    def test_holiday_after_disbursement_moves_unpaid_installments(self):
        loan = self._disbursed_loan()
        installments = loan.installment_ids.sorted('sequence')
        before = installments.mapped('date_due')
        self._holiday(before[1])
        self.assertEqual(installments[0].date_due, before[0])
        for inst, old in zip(installments[1:], before[1:]):
            self.assertEqual(inst.date_due, old + timedelta(weeks=1))
            self.assertEqual(inst.move_line_id.date_maturity, inst.date_due)
        self.assertIn('public holidays', loan.message_ids[0].body)
