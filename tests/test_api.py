# -*- coding: utf-8 -*-
import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .common import RanchiCommon


@tagged('post_install', '-at_install')
class TestOfficerApi(RanchiCommon, HttpCase):
    """The mobile app's sign-in flow: password -> device API key -> bearer calls."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.officer_user = cls.env['res.users'].create({
            'name': 'Officer One', 'login': 'officer1', 'password': 'officer1-pass',
            'company_id': cls.company.id, 'company_ids': [(4, cls.company.id)],
            'groups_id': [(4, cls.env.ref('ranchi_centre.group_ranchi_officer').id)],
        })
        cls.officer.user_id = cls.officer_user

    def _rpc(self, path, params=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        body = json.dumps({'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': params or {}})
        res = self.url_open(path, data=body, headers=headers)
        return res.json()

    def test_login_returns_device_key_and_me(self):
        out = self._rpc('/api/v1/auth/login', {'login': 'officer1', 'password': 'officer1-pass', 'deviceName': 'Tecno test'})
        self.assertNotIn('error', out, out)
        result = out['result']
        self.assertTrue(result['apiKey'])
        self.assertEqual(result['name'], 'Officer One')
        self.assertEqual([u['id'] for u in result['unions']], [self.union.id])
        key = self.env['res.users.apikeys'].search([('user_id', '=', self.officer_user.id), ('name', '=', 'Tecno test')])
        self.assertEqual(len(key), 1)

        me = self._rpc('/api/v1/me', key=result['apiKey'])
        self.assertEqual(me['result']['userId'], self.officer_user.id)

        out = self._rpc('/api/v1/auth/logout', key=result['apiKey'])
        self.assertEqual(out['result']['removed'], 1)
        self.assertFalse(key.exists())
        denied = self._rpc('/api/v1/me', key=result['apiKey'])
        self.assertIn('error', denied)

    def test_login_rejects_bad_password_and_non_officers(self):
        out = self._rpc('/api/v1/auth/login', {'login': 'officer1', 'password': 'wrong'})
        self.assertIn('error', out)
        self.env['res.users'].create({
            'name': 'Plain User', 'login': 'plain', 'password': 'plain-pass', 'company_id': self.company.id,
            'company_ids': [(4, self.company.id)]})
        out = self._rpc('/api/v1/auth/login', {'login': 'plain', 'password': 'plain-pass'})
        self.assertIn('error', out)
        self.assertFalse(self.env['res.users.apikeys'].search([('user_id.login', '=', 'plain')]))

    # ------------------------------------------------------------------
    # write routes, called as the credit officer (no accounting groups)
    # ------------------------------------------------------------------
    def _key(self):
        out = self._rpc('/api/v1/auth/login', {'login': 'officer1', 'password': 'officer1-pass', 'deviceName': 'Web test'})
        self.assertNotIn('error', out, out)
        return out['result']['apiKey']

    def _ok(self, path, params, key):
        out = self._rpc(path, params, key=key)
        self.assertNotIn('error', out, out)
        self.env.invalidate_all()
        return out['result']

    def test_officer_lists_journals_without_accounting_group(self):
        key = self._key()
        journals = self._ok('/api/v1/payment_journals', {}, key)
        self.assertIn(self.bank_journal.id, [j['id'] for j in journals])
        self.assertEqual([j['id'] for j in journals if j['isDefault']], [self.bank_journal.id])

    def test_officer_posts_collection_and_replay_is_idempotent(self):
        loan = self._disbursed_loan()
        key = self._key()
        params = {
            'unionId': self.union.id, 'journalId': self.bank_journal.id, 'idempotencyKey': 'web-col-1',
            'note': 'Cash counted 3000',
            'lines': [{'memberId': self.member.id, 'loanId': loan.id,
                       'loanAmount': loan.installment_amount, 'savingsAmount': 500.0}],
        }
        result = self._ok('/api/v1/collections/create', params, key)
        self.assertEqual(result['status'], 'posted')
        self.assertEqual(result['loanTotal'], round(loan.installment_amount, 2))
        self.assertEqual(result['savingsTotal'], 500.0)
        collection = self.env['ranchi.collection'].browse(result['id'])
        self.assertEqual(collection.create_uid, self.officer_user)
        self.assertTrue(collection.move_id.state == 'posted')
        self.assertEqual(loan.amount_paid, loan.installment_amount)
        self.assertEqual(self.member.savings_balance, 500.0)

        again = self._ok('/api/v1/collections/create', params, key)
        self.assertEqual(again['id'], result['id'])
        self.assertEqual(self.env['ranchi.collection'].search_count([('union_id', '=', self.union.id)]), 1)

    def test_officer_pays_single_installment(self):
        loan = self._disbursed_loan()
        inst = loan.installment_ids.sorted('sequence')[0]
        key = self._key()
        result = self._ok('/api/v1/installments/pay', {'installmentId': inst.id, 'idempotencyKey': 'web-pay-1'}, key)
        self.assertEqual(result['collection']['status'], 'posted')
        self.assertEqual(result['installment']['amountResidual'], 0.0)
        self.assertEqual(inst.amount_residual, 0.0)

    def test_officer_records_savings_deposit(self):
        key = self._key()
        result = self._ok('/api/v1/savings/deposit', {
            'memberId': self.member.id, 'amount': 1500.0, 'journalId': self.bank_journal.id,
            'note': 'counter', 'idempotencyKey': 'web-dep-1'}, key)
        self.assertEqual(result['type'], 'deposit')
        self.assertEqual(result['state'], 'posted')
        self.assertEqual(self.member.savings_balance, 1500.0)
        tx = self.env['ranchi.savings.transaction'].browse(result['id'])
        self.assertEqual(tx.create_uid, self.officer_user)
        self.assertEqual(tx.move_id.state, 'posted')
        account = self._ok('/api/v1/members/%d/savings' % self.member.id, {}, key)
        self.assertEqual(account['balance'], 1500.0)
        self.assertEqual([t['id'] for t in account['transactions']], [tx.id])

    def test_officer_submits_withdrawal_request(self):
        self._deposit(self.member, 20000.0)
        key = self._key()
        result = self._ok('/api/v1/savings/withdrawal/request', {
            'memberId': self.member.id, 'amount': 5000.0, 'reason': 'School fees', 'idempotencyKey': 'web-wd-1'}, key)
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['amount'], 5000.0)
        req = self.env['ranchi.withdrawal.request'].browse(result['id'])
        self.assertEqual(req.hold_transaction_id.state, 'posted')
        self.assertEqual(self.member.savings_available, 20000.0 - 5000.0 - req.fee_amount)
        listed = self._ok('/api/v1/savings/withdrawals', {'memberId': self.member.id}, key)
        self.assertEqual([w['id'] for w in listed], [req.id])

    def test_officer_creates_member_in_applied_state(self):
        key = self._key()
        result = self._ok('/api/v1/members/create', {
            'name': 'Rukia Bala', 'unionId': self.union.id, 'contactNumber': '08032217745',
            'nin': '22134478905', 'bvn': '22019945771', 'address': '22 Oke-Ado Street',
            'landmark': 'Opposite the water tank', 'buildingType': 'bungalow', 'yearsOfOccupancy': 4,
            'typeOfItems': 'Provisions', 'nextOfKin': 'Sadiq Bala', 'nextOfKinRelationship': 'Husband',
            'nextOfKinPhone': '08057741129', 'idempotencyKey': 'web-mem-1'}, key)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['unionId'], self.union.id)
        partner = self.env['res.partner'].browse(result['id'])
        self.assertTrue(partner.is_ranchi_member)
        self.assertEqual(partner.company_id, self.company)
        self.assertEqual(partner.nin, '22134478905')
        members = self._ok('/api/v1/unions/%d/members' % self.union.id, {}, key)
        self.assertIn(partner.id, [m['id'] for m in members])

    def test_officer_submits_loan_application(self):
        applicant = self._make_member('Member B')
        key = self._key()
        result = self._ok('/api/v1/loans/create', {
            'memberId': applicant.id, 'loanTypeId': self.loan_type.id, 'amount': 8000.0,
            'purpose': 'Restock', 'guarantorName': 'Chika Obi', 'guarantorRelationship': 'Sister',
            'guarantorPhone': '08064427781', 'submit': True, 'idempotencyKey': 'web-loan-1'}, key)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['amountApplied'], 8000.0)
        loan = self.env['ranchi.loan'].browse(result['id'])
        self.assertEqual(loan.create_uid, self.officer_user)
        self.assertEqual(loan.guarantor_name, 'Chika Obi')
        listed = self._ok('/api/v1/members/%d/loans' % applicant.id, {}, key)
        self.assertEqual([l['id'] for l in listed], [loan.id])

    def test_officer_is_limited_to_own_unions(self):
        other_officer = self.env['hr.employee'].create({'name': 'Officer Two', 'company_id': self.company.id})
        other_union = self.env['ranchi.union'].create({
            'name': 'Other Union', 'company_id': self.company.id, 'union_day': '2',
            'loan_type_id': self.loan_type.id, 'credit_officer_id': other_officer.id})
        other_member = self.env['res.partner'].create({
            'name': 'Other Member', 'is_ranchi_member': True, 'union_id': other_union.id,
            'company_id': self.company.id, 'nin': '99988877766'})
        key = self._key()
        unions = self._ok('/api/v1/unions', {}, key)
        self.assertEqual([u['id'] for u in unions], [self.union.id])
        self.assertEqual(unions[0]['loanTypeId'], self.loan_type.id)
        self.assertEqual(unions[0]['meetingFrequency'], 'weekly')
        self.assertNotIn(other_member.id, [m['id'] for m in self._ok('/api/v1/members', {}, key)])
        self.assertIn('error', self._rpc('/api/v1/members/create', {'name': 'X', 'unionId': other_union.id}, key=key))
        self.assertIn('error', self._rpc('/api/v1/savings/deposit', {'memberId': other_member.id, 'amount': 100.0}, key=key))
        self.assertIn('error', self._rpc('/api/v1/collections/create', {
            'unionId': other_union.id, 'lines': [{'memberId': other_member.id, 'savingsAmount': 100.0}]}, key=key))
        self.assertEqual(self.env['ranchi.savings.transaction'].search_count([('member_id', '=', other_member.id)]), 0)
