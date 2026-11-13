# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestMemberExit(RanchiCommon):
    """Members leave only with no loan and no savings; a returning member registers again as new."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.loan_type.write({'fee_ids': [
            (0, 0, {'name': 'Registration form', 'stage': 'registration', 'amount': 800.0})]})

    def _repay_all(self, loan):
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': loan.member_id.id, 'loan_id': loan.id, 'amount_loan': loan.balance})],
        })
        self._hand_over(collection)

    def _new_member(self, name):
        partner = self._make_member(name, confirm=False)
        self._collect_fees(partner, 800.0)
        if partner.membership_state == 'applied':
            partner.action_vet_member()
        partner.action_confirm_member()
        return partner

    def test_exit_with_nothing_open(self):
        member = self._new_member('Leaver')
        self.assertTrue(member.can_exit)
        member.action_exit_member(reason="Moved to Kano")
        self.assertEqual(member.membership_state, 'exited')
        self.assertTrue(member.exit_date)
        self.assertEqual(member.exit_reason, "Moved to Kano")
        self.assertFalse(member.can_exit)
        with self.assertRaises(UserError):
            member.action_exit_member()

    def test_exit_blocked_by_open_loan(self):
        member = self._new_member('Borrower')
        loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 5000.0})
        loan.action_apply()
        self.assertFalse(member.can_exit)
        self.assertIn(loan.name, member._exit_block())
        with self.assertRaises(UserError):
            member.action_exit_member()

    def test_exit_blocked_by_savings_and_pending_cash(self):
        member = self._new_member('Saver')
        self._deposit(member, 500.0)
        with self.assertRaises(UserError):
            member.action_exit_member()

        other = self._new_member('Cash In Transit')
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': other.id, 'amount_savings': 200.0})],
        })
        collection.action_submit()
        self.assertTrue(other._exit_block())
        with self.assertRaises(UserError):
            other.action_exit_member()

    def test_applicant_exit_cancels_unpaid_registration_invoice(self):
        applicant = self._make_member('Dropped Out', confirm=False)
        invoice = applicant._create_registration_invoice()
        applicant.action_exit_member()
        self.assertEqual(applicant.membership_state, 'exited')
        self.assertEqual(invoice.state, 'cancel')

    def test_part_paid_registration_blocks_exit(self):
        applicant = self._make_member('Part Paid', confirm=False)
        self._collect_fees(applicant, 300.0)
        with self.assertRaises(UserError):
            applicant.action_exit_member()

    def test_exited_member_cannot_save_or_borrow(self):
        member = self._new_member('Gone')
        member.action_exit_member()
        with self.assertRaises(UserError):
            self._deposit(member, 100.0)
        with self.assertRaises(UserError):
            self.env['ranchi.loan'].create({
                'member_id': member.id, 'company_id': self.company.id,
                'loan_type_id': self.loan_type.id, 'amount_applied': 5000.0})
        with self.assertRaises(ValidationError):
            self.env['ranchi.collection'].create({
                'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
                'line_ids': [(0, 0, {'member_id': member.id, 'amount_savings': 100.0})],
            })

    def test_rejoin_registers_as_new_member(self):
        member = self._new_member('Returner')
        loan = self._disbursed_loan(member, amount=5000.0)
        self._repay_all(loan)
        self.assertEqual(loan.state, 'paid')
        self.assertEqual(member.loan_cycle, 1)
        old_number, old_invoice = member.member_number, member.membership_fee_move_id

        member.action_exit_member()
        with self.assertRaises(UserError):
            member.action_reset_to_applied()
        member.action_rejoin_member()

        self.assertEqual(member.membership_state, 'applied')
        self.assertEqual(member.membership_term, 2)
        self.assertFalse(member.member_number)
        self.assertFalse(member.membership_fee_move_id)
        self.assertEqual(member.loan_cycle, 0, "repaid loans of the old membership do not count")
        self.assertEqual(loan.membership_term, 1)
        # registration fees are due again in full, like any new member
        self.assertEqual(member.registration_fee_residual, 800.0)
        self.assertEqual(member._fees_due_total(), 800.0)
        member.action_vet_member()
        with self.assertRaises(UserError):
            member.action_confirm_member()

        self._collect_fees(member, 800.0)
        self.assertNotEqual(member.membership_fee_move_id, old_invoice)
        if member.membership_state == 'applied':
            member.action_vet_member()
        member.action_confirm_member()
        self.assertEqual(member.membership_state, 'confirmed')
        self.assertTrue(member.member_number)
        self.assertNotEqual(member.member_number, old_number)

        new_loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': 5000.0})
        self.assertEqual(new_loan.membership_term, 2)

    def test_only_exited_members_rejoin(self):
        with self.assertRaises(UserError):
            self.member.action_rejoin_member()
