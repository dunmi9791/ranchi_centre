# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestSavingsAdjustment(RanchiCommon):
    """Officer requests paying a loan from savings; a manager of the product confirms and posts it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def user(login, *groups):
            return cls.env['res.users'].create({
                'name': login, 'login': login,
                'company_id': cls.company.id, 'company_ids': [(4, cls.company.id)],
                'groups_id': [(4, cls.env.ref(g).id) for g in groups],
            })
        cls.officer_user = user('officer_adjust', 'ranchi_centre.group_ranchi_officer')
        cls.officer.user_id = cls.officer_user
        cls.manager = user('adjust_mgr', 'ranchi_centre.group_ranchi_manager')
        cls.other_manager = user('other_mgr', 'ranchi_centre.group_ranchi_manager')
        cls.loan_type.manager_ids = cls.manager

    def _request(self, loan, amount, settle_in_full=False):
        adj = self.env['ranchi.lapse.adjustment'].with_user(self.officer_user).create({
            'member_id': self.member.id, 'loan_id': loan.id, 'company_id': self.company.id,
            'savings_used': amount, 'settle_in_full': settle_in_full})
        adj.action_submit()
        return adj

    def test_officer_requests_and_manager_confirms_partial(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        adj = self._request(loan, 1100.0)
        self.assertEqual(adj.state, 'submitted')
        self.assertEqual(adj.requested_by_id, self.officer_user)
        self.assertEqual(adj.hold_transaction_id.state, 'posted')
        self.assertAlmostEqual(self.member.savings_available, 1900.0)
        self.assertFalse(adj.move_id, "nothing is booked before the manager confirms")

        adj.with_user(self.manager).action_confirm()
        self.assertEqual(adj.state, 'done')
        self.assertEqual(adj.confirmed_by_id, self.manager)
        self.assertEqual(adj.move_id.state, 'posted')
        self.assertEqual(adj.hold_transaction_id.state, 'cancelled')
        self.assertAlmostEqual(self.member.savings_balance, 1900.0)
        self.assertAlmostEqual(self.member.savings_available, 1900.0)
        self.assertAlmostEqual(loan.amount_paid, 1100.0)
        self.assertEqual(loan.state, 'disbursed')
        self.assertEqual(loan.installment_ids.sorted('sequence')[0].state, 'paid')

    def test_settle_in_full_collects_cash_shortfall(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        adj = self._request(loan, 3000.0, settle_in_full=True)
        self.assertAlmostEqual(adj.shortfall, 4400.0 - 3000.0)
        adj.with_user(self.manager).write({'journal_id': self.bank_journal.id})
        adj.with_user(self.manager).action_confirm()
        self.assertEqual(loan.state, 'paid')
        self.assertAlmostEqual(adj.cash_amount, 1400.0)
        self.assertAlmostEqual(self.member.savings_balance, 0.0)

    def test_officer_cannot_confirm_or_edit_request(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        adj = self._request(loan, 1000.0)
        with self.assertRaises(AccessError):
            adj.with_user(self.officer_user).action_confirm()
        with self.assertRaises(AccessError):
            adj.with_user(self.officer_user).write({'state': 'done'})
        with self.assertRaises(UserError):
            adj.with_user(self.officer_user).write({'savings_used': 2000.0})
        with self.assertRaises(AccessError):
            adj.with_user(self.other_manager).action_confirm()
        self.assertEqual(adj.state, 'submitted')

    def test_request_cannot_exceed_savings_or_balance(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        with self.assertRaises(UserError):
            self._request(loan, 3500.0)
        self._deposit(self.member, 5000.0)
        with self.assertRaises(UserError):
            self._request(loan, 5000.0)

    def test_reject_and_cancel_release_the_hold(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        adj = self._request(loan, 1000.0)
        adj.with_user(self.manager).write({'rejection_reason': 'Member must sign first'})
        adj.with_user(self.manager).action_reject()
        self.assertEqual(adj.state, 'rejected')
        self.assertEqual(adj.hold_transaction_id.state, 'cancelled')
        self.assertAlmostEqual(self.member.savings_available, 3000.0)

        again = self._request(loan, 2000.0)
        again.with_user(self.officer_user).action_cancel()
        self.assertEqual(again.state, 'cancelled')
        self.assertAlmostEqual(self.member.savings_available, 3000.0)
