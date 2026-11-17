# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models, tools
from odoo.exceptions import AccessError

LOAN_KPIS = ('kpi_ranchi_disbursed', 'kpi_ranchi_collected', 'kpi_ranchi_defaults')
BOOKED_LOAN_STATES = ('disbursed', 'paid', 'written_off')
PERIODS = {
    'daily': relativedelta(days=1),
    'weekly': relativedelta(weeks=1),
    'monthly': relativedelta(months=1),
    'quarterly': relativedelta(months=3),
}


class DigestDigest(models.Model):
    """Loan KPIs for the periodic digest email, with a credit officer / union breakdown.

    A period runs over whole days: from the start date included to the end date excluded,
    so a daily digest sent this morning reports yesterday.

    * Disbursed: approved amount of loans whose disbursement date falls in the period.
    * Collected: everything taken in the period, split into
      - loans: repayments (write-offs and savings adjustments excluded: no new cash),
      - savings: posted savings deposits,
      - fees: fees on field collections (the only way fees are paid),
      plus loan and savings cash on field collections submitted in the period and not yet
      confirmed by a manager, as on the Daily Collection Sheet.
    * Defaults: what is still unpaid at the end of the period of the installments that fell
      due in it. Payments clear the oldest installments first, so a member paying late
      arrears does not cover the period's installment.
    """
    _inherit = 'digest.digest'

    kpi_ranchi_disbursed = fields.Boolean(string="Total Loans Disbursed")
    kpi_ranchi_disbursed_value = fields.Monetary(compute='_compute_kpi_ranchi_disbursed_value')
    kpi_ranchi_collected = fields.Boolean(string="Total Collections (Loans, Savings, Fees)")
    kpi_ranchi_collected_value = fields.Monetary(compute='_compute_kpi_ranchi_collected_value')
    kpi_ranchi_defaults = fields.Boolean(string="Total Defaults")
    kpi_ranchi_defaults_value = fields.Monetary(compute='_compute_kpi_ranchi_defaults_value')

    # ------------------------------------------------------------------
    # KPI values (one column per digest timeframe)
    # ------------------------------------------------------------------
    def _ranchi_check_access(self):
        user = self.env.user
        if not (user.has_group('ranchi_centre.group_ranchi_officer')
                or user.has_group('ranchi_centre.group_ranchi_auditor')):
            raise AccessError(_("Do not have access, skip this data for user's digest email"))

    def _ranchi_kpi_period(self):
        """Date range of the timeframe being computed, end excluded."""
        start = self.env.context.get('start_datetime')
        end = self.env.context.get('end_datetime')
        if not (start and end):
            today = fields.Date.context_today(self)
            return today - timedelta(days=1), today
        return start.date(), end.date()

    def _ranchi_kpi_companies(self):
        companies = self.company_id
        if any(not digest.company_id for digest in self):
            companies |= self.env.company
        return companies

    def _ranchi_set_kpi(self, field_name, key):
        self._ranchi_check_access()
        date_from, date_to = self._ranchi_kpi_period()
        rows = self._ranchi_union_figures(date_from, date_to, self._ranchi_kpi_companies())
        per_company = defaultdict(float)
        for union, figures in rows.items():
            per_company[union.company_id.id] += figures[key]
        for digest in self:
            company = digest.company_id or self.env.company
            digest[field_name] = per_company.get(company.id, 0.0)

    def _compute_kpi_ranchi_disbursed_value(self):
        self._ranchi_set_kpi('kpi_ranchi_disbursed_value', 'disbursed')

    def _compute_kpi_ranchi_collected_value(self):
        self._ranchi_set_kpi('kpi_ranchi_collected_value', 'collected')

    def _compute_kpi_ranchi_defaults_value(self):
        self._ranchi_set_kpi('kpi_ranchi_defaults_value', 'defaults')

    def _compute_kpis_actions(self, company, user):
        res = super()._compute_kpis_actions(company, user)
        menu = self.env.ref('ranchi_centre.menu_ranchi_root', raise_if_not_found=False)
        suffix = f'?menu_id={menu.id}' if menu else ''
        res['kpi_ranchi_disbursed'] = f'ranchi_centre.action_ranchi_loan{suffix}'
        res['kpi_ranchi_collected'] = f'ranchi_centre.action_ranchi_collection{suffix}'
        res['kpi_ranchi_defaults'] = f'ranchi_centre.action_ranchi_loan_installment{suffix}'
        return res

    # ------------------------------------------------------------------
    # Figures per union
    # ------------------------------------------------------------------
    def _ranchi_union_figures(self, date_from, date_to, companies):
        """union -> {'disbursed', 'loans', 'collected', 'collected_loans', 'collected_savings',
        'collected_fees', 'defaults', 'defaulters'} for
        ``date_from <= date < date_to``, read with the current user's record rules."""
        base = [('company_id', 'in', companies.ids)]
        rows = defaultdict(lambda: {'disbursed': 0.0, 'loans': 0, 'collected': 0.0, 'collected_loans': 0.0,
                                    'collected_savings': 0.0, 'collected_fees': 0.0,
                                    'defaults': 0.0, 'defaulters': 0})

        Loan = self.env['ranchi.loan']
        for union, amount, count in Loan._read_group(
                base + [('state', 'in', BOOKED_LOAN_STATES),
                        ('date_disbursed', '>=', date_from), ('date_disbursed', '<', date_to)],
                ['union_id'], ['amount_approved:sum', '__count']):
            rows[union]['disbursed'] += amount or 0.0
            rows[union]['loans'] += count

        period = [('date', '>=', date_from), ('date', '<', date_to)]
        Repayment = self.env['ranchi.loan.repayment']
        for union, amount in Repayment._read_group(
                base + period + [('source', 'not in', ('writeoff', 'lapse'))],
                ['union_id'], ['amount:sum']):
            rows[union]['collected_loans'] += amount or 0.0
        Savings = self.env['ranchi.savings.transaction']
        for union, amount in Savings._read_group(
                base + period + [('type', '=', 'deposit'), ('state', '=', 'posted')],
                ['union_id'], ['amount:sum']):
            rows[union]['collected_savings'] += amount or 0.0
        # Field collections: fees whether confirmed or not (posted fees have no other record
        # to count), loan and savings cash only while it waits for the manager.
        Line = self.env['ranchi.collection.line']
        for collection, state, loans, savings, fees in Line._read_group(
                base + period + [('state', 'in', ('submitted', 'posted'))],
                ['collection_id', 'state'], ['amount_loan:sum', 'amount_savings:sum', 'amount_fees:sum']):
            row = rows[collection.union_id]
            row['collected_fees'] += fees or 0.0
            if state == 'submitted':
                row['collected_loans'] += loans or 0.0
                row['collected_savings'] += savings or 0.0
        for row in rows.values():
            row['collected'] = row['collected_loans'] + row['collected_savings'] + row['collected_fees']

        for union, (amount, members) in self._ranchi_defaults(date_from, date_to, base).items():
            rows[union]['defaults'] += amount
            rows[union]['defaulters'] += len(members)

        return {union: vals for union, vals in rows.items()
                if any(vals[k] for k in ('disbursed', 'collected', 'defaults'))}

    def _ranchi_defaults(self, date_from, date_to, base):
        """union -> (unpaid amount, member ids) of installments due in the period."""
        Installment = self.env['ranchi.loan.installment']
        loans = Installment.search(base + [
            ('loan_state', 'in', BOOKED_LOAN_STATES),
            ('date_due', '>=', date_from), ('date_due', '<', date_to),
        ]).loan_id
        result = defaultdict(lambda: [0.0, set()])
        if not loans:
            return {}
        # Every installment of those loans due before the end, so older arrears absorb
        # payments first, as on the Daily Collection Sheet.
        installments = Installment.search([
            ('loan_id', 'in', loans.ids), ('date_due', '<', date_to)], order='loan_id, date_due, sequence')
        settled = dict(self.env['ranchi.loan.repayment']._read_group(
            [('loan_id', 'in', loans.ids), ('source', '!=', 'writeoff'), ('date', '<', date_to)],
            ['loan_id'], ['amount:sum']))
        pending = dict(self.env['ranchi.collection.line']._read_group(
            [('loan_id', 'in', loans.ids), ('state', '=', 'submitted'), ('date', '<', date_to)],
            ['loan_id'], ['amount_loan:sum']))
        for loan, insts in installments.grouped('loan_id').items():
            paid = (settled.get(loan) or 0.0) + (pending.get(loan) or 0.0)
            unpaid = 0.0
            for inst in insts:
                share = min(paid, inst.amount_total)
                paid -= share
                if inst.date_due >= date_from:
                    unpaid += inst.amount_total - share
            if not loan.currency_id.is_zero(unpaid) and unpaid > 0:
                entry = result[loan.union_id]
                entry[0] += unpaid
                entry[1].add(loan.member_id.id)
        return {union: tuple(vals) for union, vals in result.items()}

    # ------------------------------------------------------------------
    # Breakdown table in the email
    # ------------------------------------------------------------------
    def _ranchi_breakdown_period(self, company):
        now = datetime.utcnow()
        tz_name = company.resource_calendar_id.tz
        if tz_name:
            now = pytz.utc.localize(now).astimezone(pytz.timezone(tz_name))
        end = now.date()
        start = (now - PERIODS.get(self.periodicity, PERIODS['weekly'])).date()
        return start, end

    def _ranchi_digest_breakdown(self, company, user):
        """Disbursed / collected / defaults per credit officer and union for the digest's
        own period, or None when the digest has no loan KPI or the user cannot read loans."""
        self.ensure_one()
        if not any(self[name] for name in LOAN_KPIS):
            return None
        digest = self.with_user(user).with_company(company)
        try:
            digest._ranchi_check_access()
            date_from, date_to = digest._ranchi_breakdown_period(company)
            rows = digest._ranchi_union_figures(date_from, date_to, company)
        except AccessError:
            return None
        currency = company.currency_id

        def money(value):
            return self._format_currency_amount(tools.misc.format_decimalized_amount(value), currency)

        def zero():
            return {'disbursed': 0.0, 'loans': 0, 'collected': 0.0, 'collected_loans': 0.0,
                    'collected_savings': 0.0, 'collected_fees': 0.0, 'defaults': 0.0, 'defaulters': 0}

        def add(totals, figures):
            for key in totals:
                totals[key] += figures[key]

        def formatted(figures):
            return {
                'disbursed': money(figures['disbursed']),
                'loans': figures['loans'],
                'collected': money(figures['collected']),
                'collected_loans': money(figures['collected_loans']),
                'collected_savings': money(figures['collected_savings']),
                'collected_fees': money(figures['collected_fees']),
                'defaults': money(figures['defaults']),
                'defaulters': figures['defaulters'],
            }

        officers = defaultdict(list)
        for union, figures in rows.items():
            officers[union.credit_officer_id].append((union, figures))
        grand = zero()
        officer_rows = []
        # Officers are hr.employee records, which not every reader may open.
        for officer, unions in sorted(officers.items(), key=lambda kv: (not kv[0], kv[0].sudo().name or '')):
            subtotal = zero()
            union_rows = []
            for union, figures in sorted(unions, key=lambda u: u[0].name or ''):
                add(subtotal, figures)
                union_rows.append({'name': union.name or _("No union"), **formatted(figures)})
            add(grand, subtotal)
            officer_rows.append({
                'name': officer.sudo().name or _("No credit officer"),
                'unions': union_rows,
                **formatted(subtotal),
            })
        last_day = date_to - timedelta(days=1)
        return {
            'period': _("%(start)s to %(end)s", start=tools.format_date(self.env, date_from),
                        end=tools.format_date(self.env, last_day))
            if date_from != last_day else tools.format_date(self.env, date_from),
            'show_disbursed': self.kpi_ranchi_disbursed,
            'show_collected': self.kpi_ranchi_collected,
            'show_defaults': self.kpi_ranchi_defaults,
            'officers': officer_rows,
            'totals': formatted(grand),
        }
