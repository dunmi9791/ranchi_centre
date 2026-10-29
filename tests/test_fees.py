# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install')
class TestFieldFees(RanchiCommon):
    """Registration fees, savings before approval and fees before disbursement, collected by the
    credit officer through field collections."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fee_type = cls.env['ranchi.loan.type'].create({
            'name': 'Weekly with field fees', 'service_rate': 10.0, 'service_collection': 'spread',
            'admin_charge': 500.0, 'risk_premium_rate': 1.0,
            'installment_count': 4, 'installment_period': 'weekly',
            'registration_savings': 2000.0,
            'fee_ids': [
                (0, 0, {'name': 'Registration form', 'stage': 'registration', 'amount': 1000.0, 'sequence': 1}),
                (0, 0, {'name': 'Passbook', 'stage': 'registration', 'amount': 500.0, 'sequence': 2}),
                (0, 0, {'name': 'Loan form', 'stage': 'disbursement', 'amount': 300.0}),
                (0, 0, {'name': 'Insurance', 'stage': 'disbursement', 'amount_type': 'percent', 'rate': 2.0}),
            ],
        })
        cls.fee_union = cls.env['ranchi.union'].create({
            'name': 'Fee Union', 'company_id': cls.company.id, 'union_day': '2',
            'loan_type_id': cls.fee_type.id, 'credit_officer_id': cls.officer.id})

    def _applicant(self, name='Applicant'):
        return self.env['res.partner'].create({
            'name': name, 'is_ranchi_member': True, 'union_id': self.fee_union.id,
            'company_id': self.company.id, 'nin': '22233344455'})

    def _collect(self, member, fees=0.0, savings=0.0, loan=None, loan_amount=0.0):
        collection = self.env['ranchi.collection'].create({
            'union_id': member.union_id.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': member.id, 'amount_fees': fees, 'amount_savings': savings,
                                 'loan_id': loan.id if loan else False, 'amount_loan': loan_amount})],
        })
        return self._hand_over(collection)

    # ---- loan type ------------------------------------------------------

    def test_registration_fee_must_be_fixed(self):
        with self.assertRaises(ValidationError):
            self.env['ranchi.loan.type.fee'].create({
                'loan_type_id': self.fee_type.id, 'name': 'Bad', 'stage': 'registration',
                'amount_type': 'percent', 'rate': 1.0})

    def test_loan_fee_total_includes_type_fees(self):
        member = self._applicant()
        member.membership_state = 'confirmed'
        loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id,
            'loan_type_id': self.fee_type.id, 'amount_applied': 10000.0})
        # admin 500 + risk 1% 100 + loan form 300 + insurance 2% 200
        self.assertEqual(loan.other_fee_amount, 500.0)
        self.assertEqual(loan.fee_total, 1100.0)
        self.assertEqual([i['amount'] for i in loan._fee_items()], [500.0, 100.0, 300.0, 200.0])

    # ---- registration ---------------------------------------------------

    def test_registration_dues(self):
        member = self._applicant()
        self.assertEqual(member.registration_fee_total, 1500.0)
        self.assertEqual(member.registration_fee_residual, 1500.0)
        self.assertEqual(member.registration_savings_required, 2000.0)
        self.assertEqual(member.registration_savings_due, 2000.0)
        dues = member._field_dues()
        self.assertEqual([f['due'] for f in dues['fees']], [1000.0, 500.0])
        self.assertEqual(dues['fees_total'], 1500.0)

    def test_registration_collected_in_the_field(self):
        self.company.ranchi_auto_vet_on_fee = True
        member = self._applicant()
        collection = self._collect(member, fees=1500.0, savings=2000.0)
        self.assertEqual(collection.state, 'posted')
        self.assertEqual(collection.amount_fees_total, 1500.0)
        self.assertEqual(collection.amount_total, 3500.0)
        invoice = member.membership_fee_move_id
        self.assertTrue(invoice)
        self.assertEqual(invoice.ranchi_move_type, 'membership_fee')
        self.assertEqual(invoice.amount_total, 1500.0)
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertIn(invoice, collection.line_ids.fee_move_ids)
        self.assertEqual(member.savings_balance, 2000.0)
        self.assertEqual(member.membership_state, 'vetted', "Paying the fees vets the member")
        member.action_confirm_member()
        self.assertEqual(member.membership_state, 'confirmed')

    def test_partial_registration_fees(self):
        member = self._applicant()
        self._collect(member, fees=1200.0)
        self.assertEqual(member.registration_fee_residual, 300.0)
        self.assertEqual([f['due'] for f in member._fees_due()], [300.0], "the form is paid, the passbook partly")
        self._collect(member, fees=300.0)
        self.assertEqual(member.membership_fee_move_id.payment_state, 'paid')
        self.assertEqual(member.registration_fee_residual, 0.0)

    def test_confirm_requires_fees_and_savings(self):
        member = self._applicant()
        member.action_vet_member()
        with self.assertRaisesRegex(UserError, 'registration fees'):
            member.action_confirm_member()
        self._collect(member, fees=1500.0, savings=500.0)
        with self.assertRaisesRegex(UserError, 'savings'):
            member.action_confirm_member()
        self._collect(member, savings=1500.0)
        member.action_confirm_member()
        self.assertEqual(member.membership_state, 'confirmed')

    def test_fees_cannot_exceed_due(self):
        member = self._applicant()
        with self.assertRaises(ValidationError):
            self.env['ranchi.collection'].create({
                'union_id': self.fee_union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
                'line_ids': [(0, 0, {'member_id': member.id, 'amount_fees': 1600.0})],
            })

    def test_pending_collection_reduces_field_dues(self):
        member = self._applicant()
        collection = self.env['ranchi.collection'].create({
            'union_id': self.fee_union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': member.id, 'amount_fees': 1000.0, 'amount_savings': 500.0})],
        })
        collection.action_submit()
        dues = member._field_dues()
        self.assertEqual(dues['fees_total'], 500.0)
        self.assertEqual(dues['savings_due'], 1500.0)

    def test_branch_membership_fee_without_type_fees(self):
        """A loan type without registration fees keeps the branch membership fee, which does not
        block confirmation (as before)."""
        member = self._make_member('Old style', confirm=False)
        self.assertEqual(member.registration_fee_total, 1000.0)
        self._collect(member, fees=1000.0)
        self.assertEqual(member.membership_fee_move_id.payment_state, 'paid')

    # ---- fees before disbursement ---------------------------------------

    def _approved_loan(self):
        member = self._applicant('Borrower')
        self._collect(member, fees=1500.0, savings=2000.0)
        if member.membership_state == 'applied':
            member.action_vet_member()
        member.action_confirm_member()
        loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id,
            'loan_type_id': self.fee_type.id, 'amount_applied': 10000.0})
        loan.action_apply()
        loan.action_approve()
        self.assertEqual(loan.state, 'approved')
        return loan

    def test_disbursement_fees_collected_in_the_field(self):
        loan = self._approved_loan()
        member = loan.member_id
        self.assertEqual(loan.fee_residual, 1100.0)
        dues = member._field_dues()
        self.assertEqual(dues['fees_total'], 1100.0)
        self.assertTrue(all(f['kind'] == 'loan' and f['loan'] == loan for f in dues['fees']))
        self._collect(member, fees=600.0)
        self.assertTrue(loan.fee_move_id, "the fee invoice is created when the collection posts")
        self.assertEqual(loan.fee_residual, 500.0)
        self.assertEqual(loan.state, 'approved')
        self._collect(member, fees=500.0)
        self.assertEqual(loan.fee_move_id.payment_state, 'paid')
        self.assertEqual(loan.state, 'fees', "paid fees make the loan ready for disbursement")
        self.assertEqual(member._fees_due_total(), 0.0)

    def test_collection_settles_existing_fee_invoice(self):
        loan = self._approved_loan()
        invoice = loan._create_fee_invoice()
        self._collect(loan.member_id, fees=1100.0)
        self.assertEqual(loan.fee_move_id, invoice, "no second invoice")
        self.assertEqual(loan.state, 'fees')

    def test_fill_due_adds_fee_lines(self):
        member = self._applicant()
        collection = self.env['ranchi.collection'].create({
            'union_id': self.fee_union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id})
        collection.action_fill_due_installments()
        line = collection.line_ids.filtered(lambda l: l.member_id == member)
        self.assertEqual(line.amount_fees, 1500.0)
