# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestUnionPurse(RanchiCommon):
    """Every union has a purse; union purse fees collected before disbursement fill it, and
    managers can move money in or out of it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.company
        cls.purse_account = cls.env['account.account'].with_company(company).create({
            'name': 'Union Purses', 'code': 'RC2300', 'account_type': 'liability_current',
            'company_ids': [(4, company.id)]})
        company.ranchi_union_purse_account_id = cls.purse_account
        cls.purse_type = cls.env['ranchi.loan.type'].create({
            'name': 'Weekly with purse', 'service_rate': 10.0, 'service_collection': 'spread',
            'admin_charge': 500.0, 'installment_count': 4, 'installment_period': 'weekly',
            'fee_ids': [
                (0, 0, {'name': 'Loan form', 'stage': 'disbursement', 'amount': 300.0}),
                (0, 0, {'name': 'Union purse', 'stage': 'disbursement', 'amount': 200.0,
                        'is_union_purse': True}),
            ],
        })
        cls.purse_union = cls.env['ranchi.union'].create({
            'name': 'Purse Union', 'company_id': company.id, 'union_day': '2',
            'loan_type_id': cls.purse_type.id, 'credit_officer_id': cls.officer.id})
        cls.purse_member = cls.env['res.partner'].create({
            'name': 'Purse Member', 'is_ranchi_member': True, 'union_id': cls.purse_union.id,
            'company_id': company.id, 'nin': '33344455566'})
        cls.purse_member.membership_state = 'confirmed'
        cls.purse = cls.purse_union.purse_id

    def _approved_loan(self, amount=10000.0):
        loan = self.env['ranchi.loan'].create({
            'member_id': self.purse_member.id, 'company_id': self.company.id,
            'loan_type_id': self.purse_type.id, 'amount_applied': amount})
        loan.action_apply()
        loan.action_approve()
        self.assertEqual(loan.state, 'approved')
        return loan

    def _entry(self, direction, amount, **vals):
        return self.env['ranchi.union.purse.line'].create(dict({
            'purse_id': self.purse.id, 'name': 'Test entry', 'direction': direction,
            'amount': amount, 'journal_id': self.bank_journal.id}, **vals))

    # ---- creation ------------------------------------------------------

    def test_union_gets_its_purse(self):
        union = self.env['ranchi.union'].create({
            'name': 'Brand New Union', 'company_id': self.company.id, 'union_day': '4',
            'loan_type_id': self.loan_type.id})
        purses = self.env['ranchi.union.purse'].search([('union_id', '=', union.id)])
        self.assertEqual(len(purses), 1)
        self.assertEqual(union.purse_id, purses)
        self.assertEqual(union.purse_balance, 0.0)
        union._ensure_purse()
        self.assertEqual(self.env['ranchi.union.purse'].search_count([('union_id', '=', union.id)]), 1)

    def test_purse_fee_only_before_disbursement(self):
        with self.assertRaises(ValidationError):
            self.env['ranchi.loan.type.fee'].create({
                'loan_type_id': self.purse_type.id, 'name': 'Bad', 'stage': 'registration',
                'amount': 100.0, 'is_union_purse': True})

    # ---- fees ----------------------------------------------------------

    def test_collected_purse_fee_fills_the_purse(self):
        loan = self._approved_loan()
        self.assertEqual(loan.fee_total, 1000.0)  # admin 500 + form 300 + purse 200
        self._collect_fees(self.purse_member, loan.fee_residual)
        self.assertEqual(loan.state, 'fees')
        purse_lines = loan.fee_move_id.invoice_line_ids.filtered(lambda l: l.account_id == self.purse_account)
        self.assertEqual(sum(purse_lines.mapped('price_subtotal')), 200.0)
        self.assertEqual(self.purse.balance, 200.0)
        entry = self.purse.line_ids
        self.assertEqual(len(entry), 1)
        self.assertRecordValues(entry, [{
            'origin': 'fee', 'direction': 'credit', 'amount': 200.0, 'state': 'posted',
            'loan_id': loan.id, 'move_id': loan.fee_move_id.id}])
        # paid again by the hook: still one entry
        loan._credit_union_purse()
        self.assertEqual(len(self.purse.line_ids), 1)

    def test_partial_fees_leave_the_purse_alone(self):
        loan = self._approved_loan()
        self._collect_fees(self.purse_member, 400.0)
        self.assertEqual(loan.state, 'approved')
        self.assertEqual(self.purse.balance, 0.0)
        self._collect_fees(self.purse_member, loan.fee_residual)
        self.assertEqual(loan.state, 'fees')
        self.assertEqual(self.purse.balance, 200.0)

    def test_cancelled_loan_takes_fee_back(self):
        loan = self._approved_loan()
        self._collect_fees(self.purse_member, loan.fee_residual)
        self.assertEqual(self.purse.balance, 200.0)
        loan._reverse_union_purse()
        self.assertEqual(self.purse.balance, 0.0)
        loan._reverse_union_purse()
        self.assertEqual(self.purse.balance, 0.0)
        self.assertEqual(self.purse.line_ids.filtered(lambda l: l.origin == 'reversal').amount, 200.0)

    def test_fee_entries_not_created_by_hand(self):
        with self.assertRaises(AccessError):
            self.env['ranchi.union.purse.line'].with_user(self._user('purse_mgr_x')).create({
                'purse_id': self.purse.id, 'name': 'Fake', 'origin': 'fee', 'amount': 50.0})

    # ---- manager entries -------------------------------------------------

    def _user(self, login, group='ranchi_centre.group_ranchi_manager'):
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'company_id': self.company.id, 'company_ids': [(4, self.company.id)],
            'groups_id': [(4, self.env.ref(group).id)]})

    def test_manager_entries_move_money(self):
        credit = self._entry('credit', 1000.0)
        credit.action_post()
        self.assertEqual(credit.state, 'posted')
        self.assertTrue(credit.move_id)
        purse_line = credit.move_id.line_ids.filtered(lambda l: l.account_id == self.purse_account)
        self.assertEqual(purse_line.credit, 1000.0)
        self.assertEqual(self.purse.balance, 1000.0)

        debit = self._entry('debit', 400.0)
        debit.action_post()
        self.assertEqual(self.purse.balance, 600.0)
        self.assertEqual(debit.move_id.line_ids.filtered(lambda l: l.account_id == self.purse_account).debit, 400.0)

        with self.assertRaises(UserError):
            self._entry('debit', 700.0).action_post()

        debit.action_cancel()
        self.assertEqual(debit.state, 'cancelled')
        self.assertEqual(self.purse.balance, 1000.0)
        with self.assertRaises(UserError):
            credit.amount = 5.0

    def test_only_the_products_manager_posts(self):
        manager = self._user('purse_mgr')
        other = self._user('other_mgr')
        officer = self._user('purse_officer', 'ranchi_centre.group_ranchi_officer')
        self.purse_type.manager_ids = manager
        entry = self._entry('credit', 100.0)
        with self.assertRaises(AccessError):
            entry.with_user(officer).action_post()
        with self.assertRaises(AccessError):
            entry.with_user(other).action_post()
        entry.with_user(manager).action_post()
        self.assertEqual(self.purse.balance, 100.0)

    def test_fee_entry_cannot_be_cancelled_by_hand(self):
        loan = self._approved_loan()
        self._collect_fees(self.purse_member, loan.fee_residual)
        with self.assertRaises(UserError):
            self.purse.line_ids.action_cancel()
