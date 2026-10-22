# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestPortfolioReport(RanchiCommon):

    def _row(self, loan):
        self.env.invalidate_all()
        return self.env['ranchi.portfolio.report'].search([('loan_id', '=', loan.id)])

    def test_current_loan(self):
        loan = self._disbursed_loan(amount=10000.0)
        row = self._row(loan)
        self.assertEqual(len(row), 1)
        self.assertEqual(row.union_id, self.union)
        self.assertEqual(row.credit_officer_id, self.officer)
        self.assertEqual(row.company_id, self.company)
        self.assertAlmostEqual(row.amount_disbursed, 10000.0)
        self.assertAlmostEqual(row.balance, loan.balance)
        self.assertEqual(row.days_in_arrears, 0)
        self.assertEqual(row.par_bucket, 'current')
        self.assertEqual(row.active_count, 1)
        self.assertEqual(row.arrears_count, 0)
        self.assertAlmostEqual(row.par1_balance, 0.0)

    def test_arrears_and_par(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = loan.installment_ids.sorted('date_due')[0]
        first.date_due = fields.Date.today() - timedelta(days=45 + (loan.loan_type_id.grace_days or 0))
        row = self._row(loan)
        self.assertEqual(row.overdue_count, 1)
        self.assertAlmostEqual(row.amount_overdue, first.amount_residual)
        self.assertEqual(row.days_in_arrears, 45 + (loan.loan_type_id.grace_days or 0))
        self.assertEqual(row.arrears_count, 1)
        self.assertAlmostEqual(row.par1_balance, loan.balance)
        self.assertAlmostEqual(row.par30_balance, loan.balance)
        self.assertAlmostEqual(row.par90_balance, 0.0)

        data = self.env['ranchi.portfolio.report']._portfolio_data(self.company)
        self.assertEqual(len(data['branches']), 1)
        officer = data['branches'][0]['officers'][0]
        self.assertEqual(officer['officer'], self.officer)
        union = officer['unions'][0]
        self.assertEqual(union['union'], self.union)
        self.assertEqual(union['totals']['members'], 1)
        self.assertEqual(union['totals']['active_loans'], 1)
        self.assertEqual(union['totals']['arrears_loans'], 1)
        self.assertAlmostEqual(union['totals']['par30'], loan.balance)
        self.assertEqual(union['loans'], row)
        self.assertAlmostEqual(data['totals']['balance'], loan.balance)

    def test_union_without_loans_is_listed(self):
        self.env['ranchi.union'].create({
            'name': 'Quiet Union', 'company_id': self.company.id, 'union_day': '2',
            'loan_type_id': self.loan_type.id, 'credit_officer_id': self.officer.id})
        data = self.env['ranchi.portfolio.report']._portfolio_data(self.company, officers=self.officer)
        unions = data['branches'][0]['officers'][0]['unions']
        self.assertEqual(len(unions), 2)
        quiet = next(u for u in unions if u['union'].name == 'Quiet Union')
        self.assertEqual(quiet['totals']['active_loans'], 0)
        self.assertEqual(data['totals']['unions'], 2)

    def test_print(self):
        self._disbursed_loan(amount=10000.0)
        for report_type, xml_id in (('officer', 'action_report_officer_portfolio'),
                                    ('risk', 'action_report_portfolio_risk')):
            wizard = self.env['ranchi.portfolio.report.wizard'].create({
                'report_type': report_type, 'company_ids': [(6, 0, self.company.ids)]})
            action = wizard.action_print()
            self.assertEqual(action['report_name'], f'ranchi_centre.{xml_id.replace("action_", "")}')
            html, _type = self.env['ir.actions.report']._render_qweb_html(f'ranchi_centre.{xml_id}', wizard.ids)
            self.assertIn(b'Test Union', html)
