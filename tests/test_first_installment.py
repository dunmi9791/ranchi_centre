# -*- coding: utf-8 -*-
from datetime import date, datetime, time, timedelta

import pytz

from odoo.exceptions import ValidationError
from odoo.tests import freeze_time, tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestFirstInstallment(RanchiCommon):
    """Weekly loans: the first installment is due on the first union day after disbursement.
    The test union meets on Tuesday (union_day '1')."""

    def _holiday(self, day):
        tz = pytz.timezone(self.company.resource_calendar_id.tz or 'UTC')

        def to_utc(moment):
            return tz.localize(moment).astimezone(pytz.utc).replace(tzinfo=None)
        return self.env['resource.calendar.leaves'].create({
            'name': 'Holiday', 'company_id': self.company.id, 'calendar_id': False,
            'date_from': to_utc(datetime.combine(day, time.min)),
            'date_to': to_utc(datetime.combine(day, time(23, 59, 59))),
        })

    def _dates(self, loan):
        return loan.installment_ids.sorted('sequence').mapped('date_due')

    def _disburse_on(self, day):
        with freeze_time(day):
            return self._disbursed_loan()

    def test_disbursed_on_wednesday_pays_next_tuesday(self):
        loan = self._disburse_on('2030-01-09')
        self.assertEqual(self._dates(loan),
                         [date(2030, 1, 15), date(2030, 1, 22), date(2030, 1, 29), date(2030, 2, 5)])
        self.assertEqual(loan.date_first_due, date(2030, 1, 15))

    def test_disbursed_the_day_before_union_day(self):
        loan = self._disburse_on('2030-01-07')  # Monday
        self.assertEqual(self._dates(loan)[0], date(2030, 1, 8))

    def test_disbursed_on_union_day_pays_next_week(self):
        loan = self._disburse_on('2030-01-08')  # Tuesday
        self.assertEqual(self._dates(loan)[0], date(2030, 1, 15))

    def test_first_installment_never_more_than_seven_days_away(self):
        loan = self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0})
        for offset in range(7):
            day = date(2030, 3, 4) + timedelta(days=offset)
            first = loan._next_union_day(day)
            self.assertEqual(first.weekday(), 1)
            self.assertTrue(1 <= (first - day).days <= 7, "%s -> %s" % (day, first))

    def test_late_disbursement_moves_the_schedule(self):
        with freeze_time('2030-01-02'):
            loan = self.env['ranchi.loan'].create({
                'member_id': self.member.id, 'company_id': self.company.id,
                'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0})
            self.assertEqual(loan.date_first_due, date(2030, 1, 8))
            loan.action_apply()
            loan.action_approve()
            self.assertEqual(self._dates(loan)[0], date(2030, 1, 8))
            if loan.state == 'approved':
                self._collect_fees(self.member, loan.fee_residual)
        with freeze_time('2030-01-23'):  # Wednesday, three weeks later
            loan.action_request_disbursement()
            loan.disbursement_id.action_send()
            loan.disbursement_id.action_mark_done()
        self.assertEqual(loan.state, 'disbursed')
        self.assertEqual(self._dates(loan),
                         [date(2030, 1, 29), date(2030, 2, 5), date(2030, 2, 12), date(2030, 2, 19)])
        for inst in loan.installment_ids:
            self.assertEqual(inst.move_line_id.date_maturity, inst.date_due)
        self.assertTrue(any('first union day after disbursement' in (m.body or '') for m in loan.message_ids))

    def test_holiday_on_next_union_day_moves_one_week(self):
        self._holiday(date(2030, 1, 15))
        loan = self._disburse_on('2030-01-09')
        self.assertEqual(self._dates(loan),
                         [date(2030, 1, 22), date(2030, 1, 29), date(2030, 2, 5), date(2030, 2, 12)])

    def test_first_due_must_be_on_union_day(self):
        with self.assertRaises(ValidationError):
            self.env['ranchi.loan'].create({
                'member_id': self.member.id, 'company_id': self.company.id,
                'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0,
                'date_first_due': date(2030, 1, 7)})  # Monday
