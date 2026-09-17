# -*- coding: utf-8 -*-
from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestSavings(RanchiCommon):

    def test_deposit_posts_to_liability(self):
        tx = self._deposit(self.member, 1500.0)
        self.assertEqual(tx.state, 'posted')
        liability_line = tx.move_id.line_ids.filtered(lambda l: l.account_id == self.savings_liability)
        self.assertAlmostEqual(liability_line.credit, 1500.0)
        self.assertEqual(liability_line.partner_id, self.member)
        self.assertAlmostEqual(self.member.savings_balance, 1500.0)
        self.assertAlmostEqual(self.member.savings_available, 1500.0)

    def test_withdrawal_request_hold_and_pay(self):
        self._deposit(self.member, 2000.0)
        self.company.write({'ranchi_withdrawal_flat_fee': 50.0, 'ranchi_savings_min_balance': 100.0})
        req = self.env['ranchi.withdrawal.request'].create({
            'member_id': self.member.id, 'company_id': self.company.id, 'amount': 1000.0})
        req.action_submit()
        self.assertEqual(req.state, 'submitted')
        self.assertAlmostEqual(self.member.savings_on_hold, 1050.0)
        self.assertAlmostEqual(self.member.savings_available, 950.0)
        req.action_approve()
        self.assertEqual(req.state, 'approved')
        req.action_pay(journal=self.bank_journal)
        self.assertEqual(req.state, 'paid')
        self.assertAlmostEqual(self.member.savings_on_hold, 0.0)
        self.assertAlmostEqual(self.member.savings_balance, 2000.0 - 1000.0 - 50.0)
        self.assertTrue(req.fee_transaction_id)

    def test_withdrawal_respects_minimum_balance(self):
        self._deposit(self.member, 500.0)
        self.company.ranchi_savings_min_balance = 200.0
        req = self.env['ranchi.withdrawal.request'].create({
            'member_id': self.member.id, 'company_id': self.company.id, 'amount': 400.0})
        with self.assertRaises(UserError):
            req.action_submit()

    def test_two_level_approval(self):
        self._deposit(self.member, 1000.0)
        self.company.ranchi_withdrawal_approval_levels = '2'
        req = self.env['ranchi.withdrawal.request'].create({
            'member_id': self.member.id, 'company_id': self.company.id, 'amount': 100.0})
        req.action_submit()
        req.action_approve()
        self.assertEqual(req.state, 'approved_l1')
        req.action_approve()  # test user is admin-level, so the same user may finish
        self.assertEqual(req.state, 'approved')

    def test_interest_accrual_is_idempotent(self):
        self._deposit(self.member, 12000.0)
        self.env['ranchi.savings.rate'].create({
            'name': 'Flat 12%', 'rate_type': 'flat', 'annual_rate': 12.0,
            'active_from': date(2020, 1, 1)})
        period_end = date.today().replace(day=1)
        created = self.env['ranchi.savings.rate'].accrue_interest(self.company, period_end)
        self.assertEqual(len(created), 1)
        self.assertAlmostEqual(created.amount, 120.0)
        self.assertAlmostEqual(self.member.savings_balance, 12120.0)
        again = self.env['ranchi.savings.rate'].accrue_interest(self.company, period_end)
        self.assertFalse(again)

    def test_cancel_reverses_move(self):
        tx = self._deposit(self.member, 300.0)
        tx.action_cancel()
        self.assertEqual(tx.state, 'cancelled')
        self.assertAlmostEqual(self.member.savings_balance, 0.0)
