# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestDailyReport(RanchiCommon):

    def _wizard(self, report_type, date_from=None, date_to=None, **vals):
        today = fields.Date.today()
        return self.env['ranchi.daily.report.wizard'].create({
            'report_type': report_type,
            'date_from': date_from or today,
            'date_to': date_to or date_from or today,
            'company_ids': [(6, 0, self.company.ids)],
            **vals,
        })

    def _collect(self, loan, amount, date=None, post=True):
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'date': date or fields.Date.today(),
            'line_ids': [(0, 0, {'member_id': loan.member_id.id, 'loan_id': loan.id, 'amount_loan': amount})],
        })
        if post:
            self._hand_over(collection)
        else:
            collection.action_submit()
        return collection

    def _due_today(self, loan, first_offset=0):
        """Move the schedule so the first installment falls *first_offset* days before today."""
        start = fields.Date.today() - timedelta(days=first_offset)
        step = first_offset or 7
        for n, inst in enumerate(loan.installment_ids.sorted('date_due')):
            inst.date_due = start + timedelta(days=n * step)
        return loan.installment_ids.sorted('date_due')

    def _row(self, wizard, loan):
        rows = wizard._day_rows(wizard.date_from)
        return next((r for r in rows if r['loan'] == loan), None)

    def test_disbursement_roll(self):
        loan = self._disbursed_loan(amount=10000.0)
        data = self._wizard('disbursement')._disbursement_data()
        self.assertEqual(data['totals']['loans'], 1)
        self.assertAlmostEqual(data['totals']['principal'], 10000.0)
        officer = data['branches'][0]['officers'][0]
        self.assertEqual(officer['officer'], self.officer)
        self.assertEqual(officer['unions'][0]['union'], self.union)
        self.assertEqual(officer['unions'][0]['items'][0]['loan'], loan)

        yesterday = fields.Date.today() - timedelta(days=1)
        self.assertFalse(self._wizard('disbursement', yesterday)._disbursement_data()['branches'])

    def test_unpaid_installment_is_a_default(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)[0]
        row = self._row(self._wizard('collection'), loan)
        self.assertAlmostEqual(row['expected'], first.amount_total)
        self.assertAlmostEqual(row['repaid'], 0.0)
        self.assertAlmostEqual(row['shortfall'], first.amount_total)
        sheet = self._wizard('default')._collection_data(defaults_only=True)
        self.assertEqual(sheet['totals']['defaulters'], 1)

    def test_submitted_cash_counts_as_collected(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)[0]
        self._collect(loan, first.amount_total, post=False)
        row = self._row(self._wizard('collection'), loan)
        self.assertAlmostEqual(row['repaid'], first.amount_total)
        self.assertAlmostEqual(row['pending'], first.amount_total)
        self.assertAlmostEqual(row['shortfall'], 0.0)

    def test_posted_collection(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)[0]
        self._collect(loan, first.amount_total)
        row = self._row(self._wizard('collection'), loan)
        self.assertAlmostEqual(row['expected'], first.amount_total)
        self.assertAlmostEqual(row['repaid'], first.amount_total)
        self.assertAlmostEqual(row['pending'], 0.0)
        self.assertAlmostEqual(row['shortfall'], 0.0)
        sheet = self._wizard('collection')._collection_data()
        self.assertEqual(sheet['summary'][0]['officer'], self.officer)
        self.assertAlmostEqual(sheet['totals']['met'], first.amount_total)

    def test_paid_ahead_is_not_expected(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)[0]
        self._collect(loan, first.amount_total, date=fields.Date.today() - timedelta(days=1))
        row = self._row(self._wizard('collection'), loan)
        self.assertIsNone(row)

    def test_payment_clears_arrears_first(self):
        loan = self._disbursed_loan(amount=10000.0)
        insts = self._due_today(loan, first_offset=1)  # first due yesterday, second today
        self.assertEqual(insts[1].date_due, fields.Date.today())
        self._collect(loan, insts[0].amount_total)
        row = self._row(self._wizard('collection'), loan)
        self.assertAlmostEqual(row['arrears_bf'], insts[0].amount_total)
        self.assertAlmostEqual(row['expected'], insts[1].amount_total)
        self.assertAlmostEqual(row['shortfall'], insts[1].amount_total)
        self.assertEqual(row['days_late'], 1)

    def test_dates_and_print(self):
        with self.assertRaises(ValidationError):
            self._wizard('collection', fields.Date.today(), fields.Date.today() - timedelta(days=1))
        loan = self._disbursed_loan(amount=10000.0)
        self._due_today(loan)
        for report_type, xml_id in (('disbursement', 'action_report_disbursement_roll'),
                                    ('collection', 'action_report_daily_collection'),
                                    ('default', 'action_report_daily_defaults')):
            wizard = self._wizard(report_type)
            action = wizard.action_print()
            self.assertEqual(action['report_name'], f'ranchi_centre.{xml_id.replace("action_", "")}')
            html, _type = self.env['ir.actions.report']._render_qweb_html(f'ranchi_centre.{xml_id}', wizard.ids)
            self.assertIn(b'Member A', html)
            self.assertTrue(wizard.action_open_analysis()['domain'])
