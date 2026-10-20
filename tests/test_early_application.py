# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestEarlyApplication(RanchiCommon):
    """A member may apply for the next loan near the end of the current one,
    but the new loan is only disbursed once the current one is fully paid."""

    def _repay(self, loan, amount):
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': self.member.id, 'loan_id': loan.id, 'amount_loan': amount})],
        })
        self._hand_over(collection)

    def _repay_installments(self, loan, count):
        due = loan.installment_ids.sorted('date_due')[:count]
        self._repay(loan, sum(due.mapped('amount_total')))

    def _new_loan(self):
        return self.env['ranchi.loan'].create({
            'member_id': self.member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 5000.0})

    def _pay_fees(self, loan):
        loan.action_create_fee_invoice()
        if loan.state == 'approved':
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=loan.fee_move_id.ids).create({
                    'journal_id': self.bank_journal.id})._create_payments()
        self.assertEqual(loan.state, 'fees')

    def test_blocked_with_three_or_more_installments_left(self):
        self.company.ranchi_early_application_installments = 3
        loan = self._disbursed_loan()  # 4 installments
        self._repay_installments(loan, 1)  # 3 left
        self.assertFalse(self.member.can_apply_loan)
        with self.assertRaises(UserError):
            self._new_loan().action_apply()

    def test_apply_early_but_disburse_only_when_paid(self):
        self.company.ranchi_early_application_installments = 3
        loan = self._disbursed_loan()
        self._repay_installments(loan, 2)  # 2 left
        self.assertTrue(self.member.can_apply_loan)

        new = self._new_loan()
        new.action_apply()
        new.action_approve()
        self.assertEqual(new.previous_loan_id, loan)
        # the running loan stays the member's active loan for collections
        self.assertEqual(self.member.active_loan_id, loan)
        self._pay_fees(new)
        with self.assertRaises(UserError):
            new.action_request_disbursement()

        self._repay(loan, loan.balance)
        self.assertEqual(loan.state, 'paid')
        self.assertFalse(new.previous_loan_id)
        new.action_request_disbursement()
        new.disbursement_id.action_send()
        new.disbursement_id.action_mark_done()
        self.assertEqual(new.state, 'disbursed')

    def test_only_one_early_application(self):
        self.company.ranchi_early_application_installments = 3
        loan = self._disbursed_loan()
        self._repay_installments(loan, 2)
        self._new_loan().action_apply()
        self.assertFalse(self.member.can_apply_loan)
        with self.assertRaises(UserError):
            self._new_loan().action_apply()

    def test_window_zero_disables(self):
        self.company.ranchi_early_application_installments = 0
        loan = self._disbursed_loan()
        self._repay_installments(loan, 3)  # 1 left
        with self.assertRaises(UserError):
            self._new_loan().action_apply()
