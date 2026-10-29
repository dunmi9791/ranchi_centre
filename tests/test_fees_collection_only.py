# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestFeesCollectionOnly(RanchiCommon):
    """Registration fees and fees before disbursement come in only through a field collection."""

    def _approved_loan(self, member=None):
        loan = self.env['ranchi.loan'].create({
            'member_id': (member or self.member).id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 10000.0})
        loan.action_apply()
        loan.action_approve()
        self.assertEqual(loan.state, 'approved')
        return loan

    def _register_payment(self, invoice):
        return self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({
                'journal_id': self.bank_journal.id})._create_payments()

    def test_register_payment_blocked_on_loan_fees(self):
        loan = self._approved_loan()
        invoice = loan._create_fee_invoice()
        with self.assertRaises(UserError):
            invoice.action_register_payment()
        with self.assertRaises(UserError):
            self._register_payment(invoice)
        self.assertEqual(loan.state, 'approved')

    def test_register_payment_blocked_on_membership_fees(self):
        partner = self._make_member('Walk-in Member', confirm=False)
        invoice = partner._create_registration_invoice()
        with self.assertRaises(UserError):
            invoice.action_register_payment()
        with self.assertRaises(UserError):
            self._register_payment(invoice)
        self.assertEqual(partner.membership_state, 'applied')

    def test_outstanding_payment_cannot_be_matched(self):
        loan = self._approved_loan()
        invoice = loan._create_fee_invoice()
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': loan.member_id.id,
            'amount': invoice.amount_total, 'journal_id': self.bank_journal.id})
        payment.action_post()
        credit = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == invoice.line_ids.filtered(lambda x: x.display_type == 'payment_term').account_id)
        with self.assertRaises(UserError):
            invoice.js_assign_outstanding_line(credit.id)

    def test_collection_still_settles_fees(self):
        loan = self._approved_loan()
        self._collect_fees(loan.member_id, loan.fee_residual)
        self.assertEqual(loan.fee_move_id.payment_state, 'paid')
        self.assertEqual(loan.state, 'fees')

    def test_credit_note_still_reverses_fee_invoice(self):
        loan = self._approved_loan()
        invoice = loan._create_fee_invoice()
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({
                'journal_id': invoice.journal_id.id, 'reason': 'Loan cancelled'})
        reversal.refund_moves()
        refund = invoice.reversal_move_ids
        refund.action_post()
        receivable = (invoice | refund).line_ids.filtered(
            lambda l: l.display_type == 'payment_term' and not l.reconciled)
        receivable.reconcile()
        self.assertEqual(invoice.payment_state, 'reversed')

    def test_daily_report_shows_fees_due(self):
        loan = self._approved_loan()
        tomorrow = fields.Date.today() + timedelta(days=1)
        wizard = self.env['ranchi.daily.report.wizard'].create({
            'report_type': 'collection', 'date_from': fields.Date.today(), 'date_to': tomorrow,
            'company_ids': [(6, 0, self.company.ids)]})
        obligations = wizard._fee_obligations()
        rows = wizard._day_rows(fields.Date.today(), obligations, self.env['ranchi.collection.line'])
        row = next(r for r in rows if r['member'] == loan.member_id)
        self.assertEqual(row['fees_due'], loan.fee_total, "fees are due from the day the loan is approved")
        self.assertEqual(row['expected'], 0.0, "fees stay out of the loan expectation")

        self._collect_fees(loan.member_id, loan.fee_total)
        rows = wizard._day_rows(tomorrow, wizard._fee_obligations(), self.env['ranchi.collection.line'])
        self.assertFalse([r for r in rows if r['member'] == loan.member_id and r['fees_due']],
                         "nothing is due the day after the fees were collected")
