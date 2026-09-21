# -*- coding: utf-8 -*-
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class RanchiCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.company_data['company']
        cls.company = company
        Account = cls.env['account.account'].with_company(company)
        cls.loan_receivable = Account.create({
            'name': 'Loans to Members', 'code': 'RC1200', 'account_type': 'asset_receivable',
            'reconcile': True, 'company_ids': [(4, company.id)]})
        cls.savings_liability = Account.create({
            'name': 'Member Savings', 'code': 'RC2200', 'account_type': 'liability_current',
            'company_ids': [(4, company.id)]})
        cls.service_income = Account.create({
            'name': 'Service Charge Income', 'code': 'RC4100', 'account_type': 'income',
            'company_ids': [(4, company.id)]})
        cls.fee_income = Account.create({
            'name': 'Withdrawal Fee Income', 'code': 'RC4200', 'account_type': 'income_other',
            'company_ids': [(4, company.id)]})
        cls.interest_expense = Account.create({
            'name': 'Savings Interest Expense', 'code': 'RC6100', 'account_type': 'expense',
            'company_ids': [(4, company.id)]})
        cls.writeoff_account = Account.create({
            'name': 'Loan Write-offs', 'code': 'RC6200', 'account_type': 'expense',
            'company_ids': [(4, company.id)]})
        cls.bank_journal = cls.company_data['default_journal_bank']
        cls.misc_journal = cls.company_data['default_journal_misc']
        cls.sale_journal = cls.company_data['default_journal_sale']
        company.write({
            'ranchi_branch_code': 'TST',
            'ranchi_loan_receivable_account_id': cls.loan_receivable.id,
            'ranchi_savings_liability_account_id': cls.savings_liability.id,
            'ranchi_service_income_account_id': cls.service_income.id,
            'ranchi_fee_income_account_id': cls.fee_income.id,
            'ranchi_interest_expense_account_id': cls.interest_expense.id,
            'ranchi_writeoff_account_id': cls.writeoff_account.id,
            'ranchi_disbursement_journal_id': cls.bank_journal.id,
            'ranchi_collection_journal_id': cls.bank_journal.id,
            'ranchi_savings_journal_id': cls.misc_journal.id,
            'ranchi_fee_journal_id': cls.sale_journal.id,
            'ranchi_membership_fee': 1000.0,
            'ranchi_membership_product_id': cls.env.ref('ranchi_centre.product_membership_fee').id,
            'ranchi_admin_fee_product_id': cls.env.ref('ranchi_centre.product_loan_admin_fee').id,
            'ranchi_risk_premium_product_id': cls.env.ref('ranchi_centre.product_risk_premium').id,
            'ranchi_service_charge_product_id': cls.env.ref('ranchi_centre.product_service_charge').id,
            'ranchi_disbursement_provider': 'manual',
        })
        cls.env.user.write({'groups_id': [(4, cls.env.ref('ranchi_centre.group_ranchi_manager').id)]})
        cls.officer = cls.env['hr.employee'].create({'name': 'Officer One', 'company_id': company.id})
        cls.loan_type = cls.env['ranchi.loan.type'].create({
            'name': 'Weekly 10%', 'service_rate': 10.0, 'service_collection': 'spread',
            'admin_charge': 500.0, 'risk_premium_rate': 1.0,
            'installment_count': 4, 'installment_period': 'weekly'})
        cls.union = cls.env['ranchi.union'].create({
            'name': 'Test Union', 'company_id': company.id, 'union_day': '1',
            'loan_type_id': cls.loan_type.id, 'credit_officer_id': cls.officer.id})
        cls.member = cls._make_member('Member A')

    @classmethod
    def _make_member(cls, name, confirm=True):
        partner = cls.env['res.partner'].create({
            'name': name, 'is_ranchi_member': True, 'union_id': cls.union.id,
            'company_id': cls.company.id, 'nin': '11122233344'})
        if confirm:
            partner.action_vet_member()
            partner.action_confirm_member()
        return partner

    def _deposit(self, member, amount):
        tx = self.env['ranchi.savings.transaction'].create({
            'member_id': member.id, 'company_id': self.company.id, 'amount': amount,
            'type': 'deposit', 'journal_id': self.bank_journal.id})
        tx.action_post()
        return tx

    def _disbursed_loan(self, member=None, amount=10000.0):
        member = member or self.member
        loan = self.env['ranchi.loan'].create({
            'member_id': member.id, 'company_id': self.company.id,
            'loan_type_id': self.loan_type.id, 'amount_applied': amount})
        loan.action_apply()
        loan.action_approve()
        if loan.state == 'approved':
            loan.action_create_fee_invoice()
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=loan.fee_move_id.ids).create({
                    'journal_id': self.bank_journal.id})._create_payments()
        self.assertEqual(loan.state, 'fees')
        loan.action_request_disbursement()
        loan.disbursement_id.action_send()
        loan.disbursement_id.action_mark_done()
        self.assertEqual(loan.state, 'disbursed')
        return loan
