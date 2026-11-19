# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestDigest(RanchiCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.digest = cls.env.ref('ranchi_centre.digest_digest_ranchi')
        cls.today = fields.Date.today()
        cls.tomorrow = cls.today + timedelta(days=1)

    def _due_today(self, loan):
        insts = loan.installment_ids.sorted('date_due')
        for n, inst in enumerate(insts):
            inst.date_due = self.today + timedelta(days=7 * n)
        return insts[0]

    def _collect(self, loan, amount, post=True):
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': loan.member_id.id, 'loan_id': loan.id, 'amount_loan': amount})],
        })
        if post:
            self._hand_over(collection)
        else:
            collection.action_submit()
        return collection

    def _figures(self):
        return self.digest._ranchi_union_figures(self.today, self.tomorrow, self.company).get(self.union)

    def _kpi(self, field_name):
        digest = self.digest.with_company(self.company).with_context(
            start_datetime=datetime.combine(self.today, time.min),
            end_datetime=datetime.combine(self.tomorrow, time.min))
        digest.company_id = self.company
        digest.invalidate_recordset([field_name])
        return digest[field_name]

    def test_disbursed_and_default(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)
        figures = self._figures()
        self.assertAlmostEqual(figures['disbursed'], loan.amount_approved)
        self.assertEqual(figures['loans'], 1)
        self.assertAlmostEqual(figures['collected_loans'], 0.0)
        self.assertAlmostEqual(figures['defaults'], first.amount_total)
        self.assertEqual(figures['defaulters'], 1)
        self.assertAlmostEqual(self._kpi('kpi_ranchi_disbursed_value'), loan.amount_approved)
        self.assertAlmostEqual(self._kpi('kpi_ranchi_defaults_value'), first.amount_total)

    def test_submitted_cash_counts_and_clears_default(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)
        self._collect(loan, first.amount_total, post=False)
        figures = self._figures()
        self.assertAlmostEqual(figures['collected_loans'], first.amount_total)
        self.assertAlmostEqual(figures['defaults'], 0.0)
        self.assertEqual(figures['defaulters'], 0)

    def test_posted_collection(self):
        loan = self._disbursed_loan(amount=10000.0)
        first = self._due_today(loan)
        self._collect(loan, first.amount_total / 2)
        figures = self._figures()
        self.assertAlmostEqual(figures['collected_loans'], first.amount_total / 2)
        self.assertAlmostEqual(figures['defaults'], first.amount_total / 2)
        self.assertAlmostEqual(self._kpi('kpi_ranchi_collected_value'), figures['collected'])

    def test_collected_includes_savings_and_fees(self):
        loan = self._disbursed_loan(amount=10000.0)
        before = self._figures()
        # Fees before disbursement went through a field collection today.
        self.assertGreater(before['collected_fees'], 0.0)
        collection = self.env['ranchi.collection'].create({
            'union_id': self.union.id, 'company_id': self.company.id, 'journal_id': self.bank_journal.id,
            'line_ids': [(0, 0, {'member_id': loan.member_id.id, 'loan_id': loan.id,
                                 'amount_loan': 300.0, 'amount_savings': 200.0})],
        })

        def assert_added(figures):
            self.assertAlmostEqual(figures['collected_loans'] - before['collected_loans'], 300.0)
            self.assertAlmostEqual(figures['collected_savings'] - before['collected_savings'], 200.0)
            self.assertAlmostEqual(figures['collected_fees'], before['collected_fees'])
            self.assertAlmostEqual(figures['collected'] - before['collected'], 500.0)

        collection.action_submit()
        assert_added(self._figures())
        # Confirming the cash turns it into a repayment and a deposit, counted once.
        collection.amount_received = collection.amount_total
        collection.action_confirm_cash()
        assert_added(self._figures())

    def test_payment_clears_older_arrears_first(self):
        loan = self._disbursed_loan(amount=10000.0)
        insts = loan.installment_ids.sorted('date_due')
        insts[0].date_due = self.today - timedelta(days=7)
        insts[1].date_due = self.today
        self._collect(loan, insts[0].amount_total)
        # The payment today settles last week's installment; today's is still owed.
        self.assertAlmostEqual(self._figures()['defaults'], insts[1].amount_total)

    def test_breakdown_by_officer_and_union(self):
        self._disbursed_loan(amount=10000.0)
        with patch.object(type(self.digest), '_ranchi_breakdown_period',
                          lambda digest, company: (self.today, self.tomorrow)):
            data = self.digest._ranchi_digest_breakdown(self.company, self.env.user)
        self.assertEqual(len(data['officers']), 1)
        officer = data['officers'][0]
        self.assertEqual(officer['name'], self.officer.name)
        self.assertEqual(officer['unions'][0]['name'], self.union.name)
        self.assertEqual(officer['loans'], 1)

        self.digest.write({'kpi_ranchi_disbursed': False, 'kpi_ranchi_collected': False,
                           'kpi_ranchi_defaults': False})
        self.assertIsNone(self.digest._ranchi_digest_breakdown(self.company, self.env.user))

    def test_breakdown_skipped_without_ranchi_group(self):
        user = self.env['res.users'].create({
            'name': 'Nobody', 'login': 'nobody-digest', 'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertIsNone(self.digest._ranchi_digest_breakdown(self.company, user))

    def test_digest_email_renders(self):
        self._disbursed_loan(amount=10000.0)
        self.digest.user_ids = self.env.user
        with patch.object(type(self.digest), '_ranchi_breakdown_period',
                          lambda digest, company: (self.today, self.tomorrow)):
            self.digest._action_send_to_user(self.env.user, consume_tips=False)
        mail = self.env['mail.mail'].search([], order='id desc', limit=1)
        self.assertIn('Loans by Credit Officer and Union', mail.body_html)
        self.assertIn(self.union.name, mail.body_html)

    def test_figures_computed_once_per_period(self):
        """Building one email reads the 3 KPIs for 3 timeframes, current and previous, plus
        the breakdown: at most one computation per distinct period."""
        self._disbursed_loan(amount=10000.0)
        self.digest.user_ids = self.env.user
        Digest = type(self.digest)
        original = Digest._ranchi_union_figures
        periods = []

        def counting(digest, date_from, date_to, companies):
            periods.append((date_from, date_to))
            return original(digest, date_from, date_to, companies)

        with patch.object(Digest, '_ranchi_union_figures', counting):
            self.digest._action_send_to_user(self.env.user, consume_tips=False)
        self.assertTrue(periods)
        self.assertEqual(len(periods), len(set(periods)))
        self.assertLessEqual(len(periods), 7)
        self.assertNotIn('ranchi_digest_figures', self.env.cr.cache)

    def test_digest_cron_runs_before_field_work(self):
        cron = self.env.ref('digest.ir_cron_digest_scheduler_action')
        self.env['digest.digest']._ranchi_schedule_digest_cron()
        main = self.env.ref('base.main_company')
        tz = main.resource_calendar_id.tz or main.partner_id.tz or 'Africa/Lagos'
        local = pytz.utc.localize(cron.nextcall).astimezone(pytz.timezone(tz))
        self.assertEqual((local.hour, local.minute), (5, 0))
        self.assertGreater(cron.nextcall, fields.Datetime.now())
