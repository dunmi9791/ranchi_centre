# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestUnionLoanType(RanchiCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rapid_type = cls.env['ranchi.loan.type'].create({
            'name': 'Rapid Daily', 'service_rate': 8.0, 'service_collection': 'spread',
            'installment_count': 30, 'installment_period': 'daily', 'meeting_frequency': 'daily'})

    def _union(self, **vals):
        base = {'name': vals.pop('name', 'U'), 'company_id': self.company.id,
                'loan_type_id': self.loan_type.id, 'union_day': '2'}
        base.update(vals)
        return self.env['ranchi.union'].create(base)

    def test_weekly_union_needs_a_day(self):
        with self.assertRaises(ValidationError):
            self._union(name='No day', union_day=False)

    def test_daily_union_has_no_day(self):
        union = self._union(name='Rapid', loan_type_id=self.rapid_type.id, union_day=False)
        self.assertEqual(union.meeting_frequency, 'daily')
        self.assertFalse(union.union_day)
        with self.assertRaises(ValidationError):
            union.write({'union_day': '3'})

    def test_switching_to_daily_type_clears_day(self):
        union = self._union(name='Switch')
        union.loan_type_id = self.rapid_type
        self.assertFalse(union.union_day)

    def test_loan_defaults_to_union_type(self):
        loan = self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id, 'amount_applied': 10000.0})
        self.assertEqual(loan.loan_type_id, self.loan_type)

    def test_loan_must_use_union_type(self):
        with self.assertRaises(ValidationError):
            self.env['ranchi.loan'].create({
                'member_id': self.member.id, 'company_id': self.company.id,
                'loan_type_id': self.rapid_type.id, 'amount_applied': 10000.0})

    def test_cannot_change_type_with_active_loans(self):
        self._disbursed_loan()
        with self.assertRaises(UserError):
            self.union.write({'loan_type_id': self.rapid_type.id})

    def test_cannot_change_frequency_of_used_type(self):
        self._union(name='Uses weekly')
        with self.assertRaises(UserError):
            self.loan_type.write({'meeting_frequency': 'daily'})

    def test_daily_union_loan_first_due_is_next_day(self):
        union = self._union(name='Rapid', loan_type_id=self.rapid_type.id, union_day=False)
        member = self.env['res.partner'].create({
            'name': 'Rapid Member', 'is_ranchi_member': True, 'union_id': union.id,
            'company_id': self.company.id, 'nin': '55566677788'})
        member.action_vet_member()
        member.action_confirm_member()
        loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id, 'amount_applied': 10000.0})
        self.assertEqual(loan.loan_type_id, self.rapid_type)
        self.assertEqual((loan.date_first_due - loan.date_application).days, 1)
