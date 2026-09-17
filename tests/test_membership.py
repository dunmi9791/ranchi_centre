# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestMembership(RanchiCommon):

    def test_pipeline_assigns_member_number(self):
        partner = self._make_member('Pipeline Member', confirm=False)
        self.assertEqual(partner.membership_state, 'applied')
        partner.action_vet_member()
        self.assertEqual(partner.membership_state, 'vetted')
        partner.action_confirm_member()
        self.assertEqual(partner.membership_state, 'confirmed')
        self.assertTrue(partner.member_number.startswith('TST/M'))
        self.assertTrue(partner.membership_date)

    def test_identity_required_only_for_members(self):
        # a plain vendor needs neither NIN nor BVN
        vendor = self.env['res.partner'].create({'name': 'Vendor', 'is_ranchi_member': False})
        self.assertFalse(vendor.nin)
        member = self.env['res.partner'].create({
            'name': 'No ID', 'is_ranchi_member': True, 'union_id': self.union.id, 'company_id': self.company.id})
        with self.assertRaises(ValidationError):
            member.action_vet_member()

    def test_membership_fee_invoice_vets_on_payment(self):
        partner = self._make_member('Fee Member', confirm=False)
        wizard = self.env['ranchi.membership.fee.wizard'].create({
            'partner_id': partner.id, 'company_id': self.company.id, 'amount': 1000.0,
            'register_payment': True, 'journal_id': self.bank_journal.id})
        wizard.action_confirm()
        invoice = partner.membership_fee_move_id
        self.assertEqual(invoice.ranchi_move_type, 'membership_fee')
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertEqual(partner.membership_state, 'vetted')

    def test_cannot_exit_with_savings(self):
        self._deposit(self.member, 500.0)
        with self.assertRaises(UserError):
            self.member.action_exit_member()
