# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestDashboard(RanchiCommon):

    def test_figures(self):
        self._deposit(self.member, 5000.0)
        loan = self._disbursed_loan(amount=10000.0)
        data = self.env['ranchi.dashboard'].get_data()
        self.assertEqual(data['unions']['total'], 1)
        self.assertEqual(data['unions']['active'], 1)
        self.assertEqual(data['unions']['by_loan_type'], [
            {'name': self.loan_type.name, 'count': 1, 'frequency': 'weekly'}])
        self.assertEqual(data['members']['total'], 1)
        self.assertEqual(data['members']['confirmed'], 1)
        self.assertEqual(data['members']['with_active_loan'], 1)
        self.assertEqual(data['loans']['active'], 1)
        self.assertAlmostEqual(data['loans']['outstanding'], loan.balance)
        self.assertEqual(data['loans']['disbursed_this_month'], 1)
        self.assertAlmostEqual(data['loans']['disbursed_this_month_amount'], 10000.0)
        self.assertAlmostEqual(data['savings']['total'], 5000.0)
        self.assertAlmostEqual(data['savings']['deposits_this_month'], 5000.0)
        self.assertEqual(data['loans']['overdue_installments'], 0)
        self.assertEqual(data['loans']['par'], 0.0)
        self.assertEqual(len(data['monthly']), 6)
        self.assertAlmostEqual(data['monthly'][-1]['disbursed'], 10000.0)
        self.assertAlmostEqual(data['monthly'][-1]['deposits'], 5000.0)
        self.assertEqual(data['top_overdue_unions'], [])

    def test_overdue_and_par(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = loan.installment_ids.sorted('date_due')[0]
        first.date_due = '2020-01-01'
        self.env.invalidate_all()
        data = self.env['ranchi.dashboard'].get_data()
        self.assertEqual(data['loans']['overdue_installments'], 1)
        self.assertEqual(data['loans']['overdue_loans'], 1)
        self.assertAlmostEqual(data['loans']['overdue_amount'], first.amount_residual)
        self.assertAlmostEqual(data['loans']['par'], first.amount_residual / loan.balance * 100.0)
        self.assertEqual(data['top_overdue_unions'][0]['id'], self.union.id)

    def test_meeting_today_and_actions(self):
        Dashboard = self.env['ranchi.dashboard']
        today = Dashboard._today()
        self.union.union_day = str(today.weekday())
        rapid = self.env['ranchi.loan.type'].create({
            'name': 'Rapid', 'service_rate': 8.0, 'installment_count': 30,
            'installment_period': 'daily', 'meeting_frequency': 'daily'})
        self.env['ranchi.union'].create({
            'name': 'Rapid Union', 'company_id': self.company.id, 'loan_type_id': rapid.id})
        data = Dashboard.get_data()
        self.assertEqual(data['unions']['meeting_today'], 2)
        action = Dashboard.open_action('unions_today')
        self.assertEqual(action['res_model'], 'ranchi.union')
        self.assertEqual(self.env['ranchi.union'].search_count(action['domain']), 2)
        action = Dashboard.open_action('loans_active')
        self.assertEqual(action['domain'], [('state', '=', 'disbursed')])

    def test_requires_ranchi_group(self):
        user = self.env['res.users'].create({
            'name': 'Nobody', 'login': 'nobody-dash', 'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env['ranchi.dashboard'].with_user(user).get_data()
