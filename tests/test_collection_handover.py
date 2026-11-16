# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo import fields
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

    def test_resaving_unchanged_lines_does_not_block_confirm(self):
        """The web client echoes the one2many when Cash Received is saved."""
        collection, _loan = self._collection()
        collection.action_submit()
        line = collection.line_ids
        line.write({
            'member_id': line.member_id.id,
            'loan_id': line.loan_id.id,
            'amount_loan': line.amount_loan,
            'amount_savings': line.amount_savings,
            'amount_fees': line.amount_fees,
        })
        self.assertEqual(collection.state, 'submitted')
        with self.assertRaises(UserError):
            line.write({'amount_savings': line.amount_savings + 50.0})

    def test_confirm_cash_retry_does_not_double_book(self):
        collection, loan = self._collection()
        collection.action_submit()
        mine = collection.with_user(self.env.user)
        mine.amount_received = collection.amount_total
        mine.action_confirm_cash()
        move = collection.move_id
        paid = loan.amount_paid
        savings = self.member.savings_balance
        repayments = collection.line_ids.repayment_ids
        self.assertTrue(repayments)
        self.assertTrue(collection.line_ids.savings_transaction_id)

        # The button timed out in the browser but the row was posted.
        mine.action_confirm_cash()
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(collection.move_id, move)
        self.assertEqual(collection.line_ids.repayment_ids, repayments)
        self.assertEqual(collection.line_ids.savings_transaction_id.state, 'posted')
        self.assertAlmostEqual(loan.amount_paid, paid)
        self.assertAlmostEqual(self.member.savings_balance, savings)

        # The entry was stored and the status write did not stick.
        collection.sudo().write({'state': 'submitted'})
        mine.action_confirm_cash()
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(collection.move_id, move)
        moves = self.env['account.move'].search([
            ('ranchi_move_type', '=', 'collection'),
            ('ref', '=', 'Field collection %s' % collection.name),
        ])
        self.assertEqual(moves, move)
        self.assertEqual(collection.line_ids.repayment_ids, repayments)
        self.assertAlmostEqual(loan.amount_paid, paid)
        self.assertAlmostEqual(self.member.savings_balance, savings)

    def test_missing_installment_repayment_is_recovered(self):
        """One linked installment must not hide another open credit on the same entry."""
        collection, loan = self._collection()
        installments = loan.installment_ids.sorted('date_due')[:2]
        collection.line_ids.amount_loan = sum(installments.mapped('amount_residual'))
        collection.action_submit()
        collection.amount_received = collection.amount_total
        collection.action_confirm_cash()
        line = collection.line_ids
        self.assertEqual(len(line.repayment_ids), 2)
        move = collection.move_id
        paid = loan.amount_paid
        savings_tx = line.savings_transaction_id
        dropped = line.repayment_ids.sorted('id')[:1]
        credit = dropped.move_line_id
        dropped_amount = dropped.amount
        dropped.unlink()
        credit.remove_move_reconcile()
        self.assertFalse(credit.reconciled)
        self.assertEqual(len(line.repayment_ids), 1)
        self.assertAlmostEqual(loan.amount_paid, paid - dropped_amount)

        collection.sudo().write({'state': 'submitted'})
        collection.action_confirm_cash()
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(collection.move_id, move)
        self.assertEqual(line.savings_transaction_id, savings_tx)
        self.assertEqual(len(line.repayment_ids), 2)
        self.assertTrue(credit.reconciled)
        self.assertAlmostEqual(loan.amount_paid, paid)
        moves = self.env['account.move'].search([
            ('ranchi_move_type', '=', 'collection'),
            ('ref', '=', 'Field collection %s' % collection.name),
        ])
        self.assertEqual(moves, move)

    def test_partial_entry_is_not_posted_again(self):
        collection, loan = self._collection()
        collection.action_submit()
        cash = self.bank_journal.default_account_id
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': self.bank_journal.id,
            'company_id': self.company.id,
            'date': collection.date,
            'ref': 'partial %s' % collection.name,
            'line_ids': [
                (0, 0, {'name': 'partial', 'account_id': cash.id, 'debit': 1.0, 'credit': 0.0}),
                (0, 0, {
                    'name': 'partial', 'account_id': self.savings_liability.id,
                    'partner_id': self.member.id, 'debit': 0.0, 'credit': 1.0,
                }),
            ],
        })
        move.action_post()
        collection.sudo().write({
            'move_id': move.id,
            'amount_received': collection.amount_total,
            'received_by_id': self.env.uid,
            'received_date': fields.Datetime.now(),
        })
        with self.assertRaises(UserError):
            collection.action_confirm_cash()
        self.assertEqual(collection.state, 'submitted')
        self.assertEqual(collection.move_id, move)
        self.assertFalse(collection.line_ids.repayment_ids)
        self.assertAlmostEqual(loan.amount_paid, 0.0)
        self.assertAlmostEqual(self.member.savings_balance, 1.0)

    def test_unexpected_posting_error_is_a_user_error(self):
        collection, _loan = self._collection()
        collection.action_submit()
        collection.amount_received = collection.amount_total
        with patch.object(type(collection), '_create_collection_move', side_effect=RuntimeError('worker timed out')):
            with self.assertRaises(UserError) as caught:
                collection.action_confirm_cash()
        self.assertIn('worker timed out', str(caught.exception))
        self.assertEqual(collection.state, 'submitted')
        self.assertFalse(collection.move_id)
