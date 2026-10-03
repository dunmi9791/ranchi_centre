# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestCollectionHandover(RanchiCommon):
    """Officer submits the collection, a manager counts the cash and posts it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.officer_user = cls.env['res.users'].create({
            'name': 'Officer One', 'login': 'officer_handover',
            'company_id': cls.company.id, 'company_ids': [(4, cls.company.id)],
            'groups_id': [(4, cls.env.ref('ranchi_centre.group_ranchi_officer').id)],
        })
        cls.officer.user_id = cls.officer_user

    def _collection(self):
        loan = self._disbursed_loan()
        collection = self.env['ranchi.collection'].with_user(self.officer_user).create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': self.member.id, 'loan_id': loan.id,
                                 'amount_loan': loan.installment_amount, 'amount_savings': 300.0})],
        })
        return collection, loan

    def test_officer_cannot_post_or_confirm(self):
        collection, loan = self._collection()
        collection.action_submit()
        self.assertEqual(collection.state, 'submitted')
        self.assertEqual(collection.submitted_by_id, self.officer_user)
        with self.assertRaises(AccessError):
            collection.action_post()
        with self.assertRaises(AccessError):
            collection.write({'amount_received': collection.amount_total})
        with self.assertRaises(AccessError):
            collection.action_confirm_cash()
        self.assertFalse(collection.move_id)
        self.assertEqual(loan.amount_paid, 0.0)

    def test_manager_cannot_post_unsubmitted(self):
        collection, _loan = self._collection()
        with self.assertRaises(UserError):
            collection.with_user(self.env.user).action_post()
        with self.assertRaises(UserError):
            collection.with_user(self.env.user).action_confirm_cash()

    def test_submitted_collection_is_locked_for_officer(self):
        collection, _loan = self._collection()
        collection.action_submit()
        with self.assertRaises(UserError):
            collection.line_ids.write({'amount_savings': 100.0})
        with self.assertRaises(UserError):
            collection.write({'date': '2020-01-01'})
        with self.assertRaises(UserError):
            collection.action_cancel()

    def test_cash_mismatch_and_return(self):
        collection, loan = self._collection()
        collection.action_submit()
        mine = collection.with_user(self.env.user)
        mine.amount_received = collection.amount_total - 100.0
        with self.assertRaises(UserError):
            mine.action_confirm_cash()
        self.assertEqual(mine.state, 'submitted')

        mine.return_reason = 'Short by 100'
        mine.action_return_to_officer()
        self.assertEqual(collection.state, 'draft')
        self.assertEqual(collection.amount_received, 0.0)
        # officer fixes the sheet and resubmits
        collection.line_ids.amount_savings = 200.0
        collection.action_submit()
        self.assertFalse(collection.return_reason)

        mine.amount_received = mine.amount_total
        mine.action_confirm_cash()
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(collection.received_by_id, self.env.user)
        self.assertEqual(collection.move_id.state, 'posted')
        self.assertAlmostEqual(loan.amount_paid, loan.installment_amount)
        self.assertAlmostEqual(self.member.savings_balance, 200.0)
