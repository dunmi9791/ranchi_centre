# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models, tools
from odoo.tools import SQL
from odoo.exceptions import AccessError

LOAN_KPIS = ('kpi_ranchi_disbursed', 'kpi_ranchi_collected', 'kpi_ranchi_defaults')
BOOKED_LOAN_STATES = ('disbursed', 'paid', 'written_off')
DIGEST_HOUR = 5
DIGEST_TZ = 'Africa/Lagos'
# Key in ``cr.cache`` of the figures memo, alive only while one digest email is built.
FIGURES_CACHE = 'ranchi_digest_figures'
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
        rows = self._ranchi_cached_figures(date_from, date_to, self._ranchi_kpi_companies())
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

    @api.model
    def _ranchi_schedule_digest_cron(self, hour=DIGEST_HOUR):
        """Run the digest cron before field work starts (it otherwise runs at whatever time
        the digest module was installed, often during collections)."""
        cron = self.env.ref('digest.ir_cron_digest_scheduler_action', raise_if_not_found=False)
        if not cron:
            return
        company = self.env.ref('base.main_company', raise_if_not_found=False) or self.env.company
        tz = pytz.timezone(company.resource_calendar_id.tz or company.partner_id.tz or DIGEST_TZ)
        now = datetime.now(tz)
        day = now.date() if now.hour < hour else now.date() + timedelta(days=1)
        run = tz.localize(datetime.combine(day, time(hour)))
        cron.sudo().write({
            'nextcall': run.astimezone(pytz.utc).replace(tzinfo=None),
            'interval_number': 1,
            'interval_type': 'days',
        })

    def _action_send_to_user(self, user, tips_count=1, consume_tips=True):
        # Odoo reads every KPI field for 3 timeframes, current and previous period, and
        # invalidates it in between; the 3 loan KPIs share their figures, so compute each
        # period once per email instead of once per field and read.
        cache = self.env.cr.cache
        cache[FIGURES_CACHE] = {}
        try:
            return super()._action_send_to_user(user, tips_count=tips_count, consume_tips=consume_tips)
        finally:
            cache.pop(FIGURES_CACHE, None)

    # ------------------------------------------------------------------
    # Figures per union
    # ------------------------------------------------------------------
    def _ranchi_cached_figures(self, date_from, date_to, companies):
        memo = self.env.cr.cache.get(FIGURES_CACHE)
        if memo is None:
            return self._ranchi_union_figures(date_from, date_to, companies)
        key = (self.env.uid, tuple(sorted(companies.ids)), date_from, date_to)
        if key not in memo:
            memo[key] = self._ranchi_union_figures(date_from, date_to, companies)
        return memo[key]

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
            rows[union]['defaulters'] += members

        return {union: vals for union, vals in rows.items()
                if any(vals[k] for k in ('disbursed', 'collected', 'defaults'))}

    def _ranchi_defaults(self, date_from, date_to, base):
        """union -> (unpaid amount, number of members) of installments due in the period.

        One query: payments up to the end of the period (repayments other than write-offs and
        loan cash submitted but not yet confirmed) are spread over each loan's installments
        oldest first, as on the Daily Collection Sheet, so older arrears absorb them before
        the period's installments. Record rules apply through the installments searched.
        """
        Installment = self.env['ranchi.loan.installment']
        due = Installment._search(base + [
            ('loan_state', 'in', BOOKED_LOAN_STATES),
            ('date_due', '>=', date_from), ('date_due', '<', date_to),
        ])
        if due.is_empty():
            return {}
        for model in ('ranchi.loan.installment', 'ranchi.loan.repayment', 'ranchi.collection.line'):
            self.env[model].flush_model()
        self.env.cr.execute(SQL("""
            WITH loans AS (
                SELECT DISTINCT loan_id FROM ranchi_loan_installment WHERE id IN %(due)s
            ), paid AS (
                SELECT loan_id, SUM(amount) AS amount FROM (
                    SELECT rep.loan_id, rep.amount
                      FROM ranchi_loan_repayment rep
                     WHERE rep.loan_id IN (SELECT loan_id FROM loans)
                       AND rep.source != 'writeoff' AND rep.date < %(date_to)s
                    UNION ALL
                    SELECT line.loan_id, line.amount_loan
                      FROM ranchi_collection_line line
                     WHERE line.loan_id IN (SELECT loan_id FROM loans)
                       AND line.state = 'submitted' AND line.date < %(date_to)s
                ) payments
                GROUP BY loan_id
            ), inst AS (
                SELECT i.loan_id, i.union_id, i.member_id, i.date_due, i.amount_total,
                       SUM(i.amount_total) OVER (
                           PARTITION BY i.loan_id ORDER BY i.date_due, i.sequence, i.id) AS cum_due
                  FROM ranchi_loan_installment i
                 WHERE i.loan_id IN (SELECT loan_id FROM loans) AND i.date_due < %(date_to)s
            ), unpaid AS (
                SELECT inst.union_id, inst.member_id,
                       LEAST(inst.amount_total,
                             GREATEST(0, inst.cum_due - COALESCE(paid.amount, 0))) AS amount
                  FROM inst
             LEFT JOIN paid ON paid.loan_id = inst.loan_id
                 WHERE inst.date_due >= %(date_from)s
            )
            SELECT union_id, SUM(amount), COUNT(DISTINCT member_id) FILTER (WHERE amount > 0.005)
              FROM unpaid
          GROUP BY union_id
            HAVING SUM(amount) > 0.005
        """, due=due.subselect(), date_from=date_from, date_to=date_to))
        Union = self.env['ranchi.union']
        # numeric columns come back as Decimal; the other figures are floats
        return {Union.browse(union_id): (float(amount), members)
                for union_id, amount, members in self.env.cr.fetchall()}

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
            rows = digest._ranchi_cached_figures(date_from, date_to, company)
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
