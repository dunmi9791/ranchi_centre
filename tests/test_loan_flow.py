# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestLoanFlow(RanchiCommon):

    def test_schedule_and_amounts(self):
        loan = self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0})
        loan.action_apply()
        loan.action_approve()
        self.assertEqual(loan.amount_approved, 10000.0)
        self.assertEqual(loan.service_amount, 1000.0)
        self.assertEqual(loan.total_repayable, 11000.0)
        self.assertEqual(len(loan.installment_ids), 4)
        self.assertEqual(sum(loan.installment_ids.mapped('amount_total')), 11000.0)
        self.assertEqual(loan.fee_total, 500.0 + 100.0)
        self.assertEqual(loan.state, 'approved')

    def test_one_active_loan_rule(self):
        self._disbursed_loan()
        second = self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 5000.0})
        with self.assertRaises(UserError):
            second.action_apply()

    def test_disbursement_books_receivable(self):
        loan = self._disbursed_loan()
        move = loan.disbursement_move_id
        self.assertEqual(move.state, 'posted')
        receivable_lines = move.line_ids.filtered(lambda l: l.account_id == self.loan_receivable)
        self.assertEqual(len(receivable_lines), 4)
        self.assertAlmostEqual(sum(receivable_lines.mapped('debit')), 11000.0)
        bank_line = move.line_ids.filtered(lambda l: l.account_id == self.bank_journal.default_account_id)
        self.assertAlmostEqual(bank_line.credit, 10000.0)
        income_line = move.line_ids.filtered(lambda l: l.account_id == self.service_income)
        self.assertAlmostEqual(income_line.credit, 1000.0)
        self.assertTrue(all(i.move_line_id for i in loan.installment_ids))
        self.assertAlmostEqual(loan.balance, 11000.0)

    def test_collection_repays_and_closes_loan(self):
        loan = self._disbursed_loan()
        first_due = loan.installment_ids.sorted('date_due')[0]
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': self.member.id, 'loan_id': loan.id,
                                 'amount_loan': first_due.amount_total, 'amount_savings': 200.0})],
        })
        self._hand_over(collection)
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(first_due.state, 'paid')
        self.assertAlmostEqual(loan.balance, 11000.0 - first_due.amount_total)
        self.assertAlmostEqual(self.member.savings_balance, 200.0)
        self.assertEqual(len(loan.repayment_ids), 1)
        # settle the rest in one go
        rest = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': self.member.id, 'loan_id': loan.id, 'amount_loan': loan.balance})],
        })
        self._hand_over(rest)
        self.assertEqual(loan.state, 'paid')
        self.assertAlmostEqual(loan.balance, 0.0)
        self.assertEqual(self.member.loan_cycle, 1)

    def test_overpayment_rejected(self):
        loan = self._disbursed_loan()
        with self.assertRaises(Exception):
            self.env['ranchi.collection'].create({
                'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
                'line_ids': [(0, 0, {'member_id': self.member.id, 'loan_id': loan.id, 'amount_loan': 99999.0})],
            })

    def test_lapse_adjustment_from_savings(self):
        loan = self._disbursed_loan(amount=4000.0)
        self._deposit(self.member, 3000.0)
        lapse = self.env['ranchi.lapse.adjustment'].create({
            'member_id': self.member.id, 'loan_id': loan.id, 'company_id': self.company.id,
            'journal_id': self.bank_journal.id})
        self.assertAlmostEqual(lapse.savings_used, 3000.0)
        self.assertAlmostEqual(lapse.shortfall, 4400.0 - 3000.0)
        lapse.action_confirm()
        self.assertEqual(lapse.state, 'done')
        self.assertEqual(loan.state, 'paid')
        self.assertAlmostEqual(self.member.savings_balance, 0.0)

    def test_write_off(self):
        loan = self._disbursed_loan()
        loan._write_off('Member relocated')
        self.assertEqual(loan.state, 'written_off')
        self.assertTrue(loan.writeoff_move_id)
        self.assertTrue(all(i.state == 'paid' for i in loan.installment_ids))
