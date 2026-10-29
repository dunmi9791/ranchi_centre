# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

MAX_DAYS = 62


class RanchiDailyReportWizard(models.TransientModel):
    """Print wizard for the day-to-day operations reports.

    * Disbursement Master Roll: every loan disbursed in the period, by branch, credit officer
      and union.
    * Daily Collection Sheet: per day and credit officer, what each member was expected to
      repay against what was collected (loan, savings and fees).
    * Defaults of the Day: members who did not fully pay the installment due that day.
    """
    _name = 'ranchi.daily.report.wizard'
    _description = 'Ranchi Daily Operations Report'

    report_type = fields.Selection(
        [('disbursement', 'Disbursement Master Roll'),
         ('collection', 'Daily Collection Sheet'),
         ('default', 'Defaults of the Day')],
        required=True, default='disbursement')
    date_from = fields.Date(string="From", required=True, default=fields.Date.context_today)
    date_to = fields.Date(string="To", required=True, default=fields.Date.context_today)
    company_ids = fields.Many2many(
        'res.company', string="Branches", required=True,
        default=lambda self: self.env.companies,
        domain=lambda self: [('id', 'in', self.env.user.company_ids.ids)])
    credit_officer_ids = fields.Many2many(
        'hr.employee', string="Credit Officers",
        help="Leave empty for every credit officer.")
    loan_type_ids = fields.Many2many(
        'ranchi.loan.type', string="Loan Types",
        help="Leave empty for every loan type.")
    include_details = fields.Boolean(
        string="List Members", default=True,
        help="List each member under the credit officer's daily totals.")

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from > wiz.date_to:
                raise ValidationError(_("The start date must be on or before the end date."))

    def _days(self):
        self.ensure_one()
        count = (self.date_to - self.date_from).days + 1
        if count > MAX_DAYS and self.report_type != 'disbursement':
            raise UserError(_("Collection and default reports cover at most %s days at a time.", MAX_DAYS))
        return [self.date_from + timedelta(days=n) for n in range(count)]

    def _loan_domain(self):
        domain = [('company_id', 'in', self.company_ids.ids)]
        if self.credit_officer_ids:
            domain.append(('credit_officer_id', 'in', self.credit_officer_ids.ids))
        if self.loan_type_ids:
            domain.append(('loan_type_id', 'in', self.loan_type_ids.ids))
        return domain

    def action_print(self):
        self.ensure_one()
        self._days()
        xml_id = {
            'disbursement': 'ranchi_centre.action_report_disbursement_roll',
            'collection': 'ranchi_centre.action_report_daily_collection',
            'default': 'ranchi_centre.action_report_daily_defaults',
        }[self.report_type]
        return self.env.ref(xml_id).report_action(self)

    def action_open_analysis(self):
        """Open the underlying records in a list, where they can be filtered or exported to Excel."""
        self.ensure_one()
        if self.report_type == 'disbursement':
            action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_loan')
            action['domain'] = self._loan_domain() + [
                ('state', 'in', ('disbursed', 'paid', 'written_off')),
                ('date_disbursed', '>=', self.date_from), ('date_disbursed', '<=', self.date_to)]
            action['context'] = {}
            action['view_mode'] = 'list,form'
            action['views'] = [(False, 'list'), (False, 'form')]
            return action
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_loan_installment')
        domain = [('company_id', 'in', self.company_ids.ids),
                  ('date_due', '>=', self.date_from), ('date_due', '<=', self.date_to)]
        if self.credit_officer_ids:
            domain.append(('loan_id.credit_officer_id', 'in', self.credit_officer_ids.ids))
        if self.loan_type_ids:
            domain.append(('loan_id.loan_type_id', 'in', self.loan_type_ids.ids))
        if self.report_type == 'default':
            domain.append(('state', '!=', 'paid'))
        action['domain'] = domain
        action['context'] = {}
        return action

    # ------------------------------------------------------------------
    # Disbursement master roll
    # ------------------------------------------------------------------
    @api.model
    def _tree(self, items, key_union, make_totals, add_totals):
        """Group *items* into branch > credit officer > union, with totals at every level.

        *key_union* maps an item to its union (may be empty), *make_totals* builds the totals
        of a list of items and *add_totals* sums two totals dicts into the first.
        """
        by_union = {}
        for item in items:
            by_union.setdefault(key_union(item), []).append(item)
        Officer = self.env['hr.employee']
        tree = {}
        for union, union_items in by_union.items():
            company = union.company_id if union else union_items[0]['company']
            officer = union.credit_officer_id if union else Officer
            tree.setdefault(company, {}).setdefault(officer, []).append({
                'union': union, 'items': union_items, 'totals': make_totals(union_items)})

        branches = []
        grand = make_totals([])
        for company in sorted(tree, key=lambda c: c.name or ''):
            branch_totals = make_totals([])
            officers = []
            for officer in sorted(tree[company], key=lambda o: (not o, o.name or '')):
                unions = sorted(tree[company][officer], key=lambda u: u['union'].name or '')
                officer_totals = make_totals([])
                for union in unions:
                    add_totals(officer_totals, union['totals'])
                officers.append({'officer': officer, 'unions': unions, 'totals': officer_totals})
                add_totals(branch_totals, officer_totals)
            branches.append({'company': company, 'officers': officers, 'totals': branch_totals})
            add_totals(grand, branch_totals)
        return {'branches': branches, 'totals': grand}

    @api.model
    def _sum_into(self, totals, other):
        for key, value in other.items():
            totals[key] += value
        return totals

    def _disbursement_data(self):
        self.ensure_one()
        loans = self.env['ranchi.loan'].search(self._loan_domain() + [
            ('state', 'in', ('disbursed', 'paid', 'written_off')),
            ('date_disbursed', '>=', self.date_from), ('date_disbursed', '<=', self.date_to),
        ], order='date_disbursed, name')
        items = []
        for loan in loans:
            member = loan.member_id
            items.append({
                'loan': loan,
                'member': member,
                'company': loan.company_id,
                'union': loan.union_id,
                'phone': member.phone or member.mobile or '',
                'principal': loan.principal,
                'service': loan.service_amount,
                'fees': loan.fee_total,
                'repayable': loan.total_repayable,
                'installment': loan.installment_amount,
                'disbursement': loan.disbursement_id,
            })

        def make_totals(rows):
            return {
                'loans': len(rows),
                'principal': sum(r['principal'] for r in rows),
                'service': sum(r['service'] for r in rows),
                'fees': sum(r['fees'] for r in rows),
                'repayable': sum(r['repayable'] for r in rows),
            }
        return self._tree(items, lambda r: r['union'], make_totals, self._sum_into)

    # ------------------------------------------------------------------
    # Expected vs collected, per day
    # ------------------------------------------------------------------
    @api.model
    def _empty_day_totals(self):
        return {
            'members': 0, 'expected': 0.0, 'arrears_bf': 0.0, 'repaid': 0.0, 'from_savings': 0.0,
            'savings': 0.0, 'fees': 0.0, 'fees_due': 0.0, 'total': 0.0, 'met': 0.0, 'shortfall': 0.0,
            'defaulters': 0, 'pending': 0.0,
        }

    def _fee_obligations(self):
        """Fees the officers had to collect in the period, as
        [{'member', 'start', 'amount', 'settled': [(date, amount)]}]: registration fees from the day
        the member was registered, fees before disbursement from the day the loan was approved.
        'settled' lists what was reconciled with the invoice and when."""
        self.ensure_one()
        result = []

        def settled(invoice):
            if not invoice:
                return []
            receivable = invoice.line_ids.filtered(lambda l: l.display_type == 'payment_term')
            return [(p.max_date, p.amount) for p in receivable.matched_credit_ids]

        loans = self.env['ranchi.loan'].search(self._loan_domain() + [
            ('date_approval', '<=', self.date_to),
            '|', ('state', '=', 'approved'),
            '&', ('state', 'in', ('fees', 'disbursed', 'paid', 'written_off')), ('fee_move_id', '!=', False),
        ])
        for loan in loans:
            invoice = loan.sudo().fee_move_id
            if invoice.state == 'cancel':
                invoice = invoice.browse()
            if not invoice and loan.state != 'approved':
                continue
            result.append({
                'member': loan.member_id, 'start': loan.date_approval,
                'amount': invoice.amount_total if invoice else loan.fee_total, 'settled': settled(invoice)})

        member_domain = [
            ('is_ranchi_member', '=', True), ('union_id.company_id', 'in', self.company_ids.ids),
            '|', ('membership_fee_move_id', '!=', False), ('membership_state', 'in', ('applied', 'vetted')),
        ]
        if self.credit_officer_ids:
            member_domain.append(('union_id.credit_officer_id', 'in', self.credit_officer_ids.ids))
        if self.loan_type_ids:
            member_domain.append(('union_id.loan_type_id', 'in', self.loan_type_ids.ids))
        for member in self.env['res.partner'].search(member_domain):
            invoice = member.sudo().membership_fee_move_id
            if invoice.state == 'cancel':
                invoice = invoice.browse()
            if invoice and invoice.state == 'posted':
                amount = invoice.amount_total
            elif member._in_registration():
                amount = member.registration_fee_residual
            else:
                continue
            result.append({
                'member': member, 'start': fields.Date.to_date(member.create_date), 'amount': amount,
                'settled': settled(invoice if invoice.state == 'posted' else None)})
        return [o for o in result if o['amount'] > 0]

    @api.model
    def _fees_due_on(self, day, obligations, pending_lines):
        """Fees each member still owed when *day* began: member -> amount. *pending_lines* are
        collection lines handed over but not posted yet, which settle nothing on the invoices."""
        owed = {}
        for ob in obligations:
            if ob['start'] > day:
                continue
            left = ob['amount'] - sum(amount for date, amount in ob['settled'] if date < day)
            if left > 0:
                owed[ob['member']] = owed.get(ob['member'], 0.0) + left
        for line in pending_lines:
            if line.date < day and line.member_id in owed:
                owed[line.member_id] = max(0.0, owed[line.member_id] - line.amount_fees)
        return owed

    def _pending_fee_lines(self):
        return self.env['ranchi.collection.line'].search([
            ('state', '=', 'submitted'), ('amount_fees', '>', 0), ('date', '<', self.date_to),
            ('company_id', 'in', self.company_ids.ids)])

    def _day_rows(self, day, fee_obligations=None, pending_fee_lines=None):
        """Member rows for *day*: what was due on the loan and what came in, as the user sees it.

        Expected is the part of the installments due that day still unpaid when the day began,
        so a member who paid ahead owes nothing. Arrears B/F is the unpaid part of installments
        due earlier. Loan collected counts repayments dated that day (any source but write-off)
        plus collections submitted but not yet posted by the manager, which have no repayment
        records yet. Repayments go to the oldest installment first, so a payment clears
        arrears before the day's installment; shortfall is what is left unpaid of the day's
        installment, and a member with a shortfall is a defaulter of the day. Fees due is what
        the member still owed in fees when the day began; it is kept out of expected and shortfall.
        """
        self.ensure_one()
        if fee_obligations is None:
            fee_obligations = self._fee_obligations()
        if pending_fee_lines is None:
            pending_fee_lines = self._pending_fee_lines()
        loans = self.env['ranchi.loan'].search(self._loan_domain() + [
            ('state', 'in', ('disbursed', 'paid', 'written_off')),
            ('date_disbursed', '<=', day),
            '|', ('state', '=', 'disbursed'), ('date_closed', '>=', day),
        ])
        currency_zero = self.env.company.currency_id.is_zero
        per_loan = {}
        if loans:
            self.env['ranchi.loan.repayment'].flush_model()
            self.env['ranchi.loan.installment'].flush_model()
            self.env.cr.execute("""
                SELECT inst.loan_id, inst.date_due, inst.amount_total,
                       COALESCE(SUM(rep.amount) FILTER (WHERE rep.date < %(day)s), 0) AS settled_before,
                       COALESCE(SUM(rep.amount) FILTER (WHERE rep.date = %(day)s
                                                          AND rep.source != 'writeoff'), 0) AS paid_today
                  FROM ranchi_loan_installment inst
             LEFT JOIN ranchi_loan_repayment rep ON rep.installment_id = inst.id
                 WHERE inst.loan_id = ANY(%(loans)s) AND inst.date_due <= %(day)s
              GROUP BY inst.id
            """, {'day': day, 'loans': loans.ids})
            for loan_id, date_due, amount_total, settled_before, paid_today in self.env.cr.fetchall():
                owed = amount_total - settled_before
                if owed <= 0:
                    continue
                vals = per_loan.setdefault(loan_id, {
                    'expected': 0.0, 'arrears_bf': 0.0, 'paid_due': 0.0, 'paid_arrears': 0.0,
                    'oldest_due': None, 'repaid': 0.0, 'from_savings': 0.0, 'pending': 0.0})
                if date_due == day:
                    vals['expected'] += owed
                    vals['paid_due'] += min(paid_today, owed)
                else:
                    vals['arrears_bf'] += owed
                    vals['paid_arrears'] += min(paid_today, owed)
                    if not vals['oldest_due'] or date_due < vals['oldest_due']:
                        vals['oldest_due'] = date_due

            self.env.cr.execute("""
                SELECT loan_id, SUM(amount), SUM(amount) FILTER (WHERE source = 'lapse')
                  FROM ranchi_loan_repayment
                 WHERE loan_id = ANY(%(loans)s) AND date = %(day)s AND source != 'writeoff'
              GROUP BY loan_id
            """, {'day': day, 'loans': loans.ids})
            for loan_id, amount, from_savings in self.env.cr.fetchall():
                vals = per_loan.setdefault(loan_id, {
                    'expected': 0.0, 'arrears_bf': 0.0, 'paid_due': 0.0, 'paid_arrears': 0.0,
                    'oldest_due': None, 'repaid': 0.0, 'from_savings': 0.0, 'pending': 0.0})
                vals['repaid'] += amount or 0.0
                vals['from_savings'] += from_savings or 0.0

        line_domain = [
            ('date', '=', day), ('state', 'in', ('submitted', 'posted')),
            ('company_id', 'in', self.company_ids.ids)]
        if self.credit_officer_ids:
            line_domain.append(('collection_id.union_id.credit_officer_id', 'in', self.credit_officer_ids.ids))
        if self.loan_type_ids:
            line_domain.append(('collection_id.loan_type_id', 'in', self.loan_type_ids.ids))
        lines = self.env['ranchi.collection.line'].search(line_domain)

        Loan = self.env['ranchi.loan']
        rows = {}  # (member, loan) -> row
        for loan in Loan.browse(list(per_loan)):
            vals = per_loan[loan.id]
            rows[(loan.member_id, loan)] = dict(vals, member=loan.member_id, loan=loan, union=loan.union_id,
                                                company=loan.company_id, savings=0.0, fees=0.0, fees_due=0.0)
        for line in lines:
            loan = line.loan_id if line.loan_id in loans else Loan
            key = (line.member_id, loan)
            if key not in rows:
                # Savings or fees only: attach to the member's loan row when there is one.
                key = next((k for k in rows if k[0] == line.member_id), key)
            row = rows.setdefault(key, {
                'expected': 0.0, 'arrears_bf': 0.0, 'paid_due': 0.0, 'paid_arrears': 0.0,
                'oldest_due': None, 'repaid': 0.0, 'from_savings': 0.0, 'pending': 0.0,
                'member': line.member_id, 'loan': loan, 'union': line.collection_id.union_id,
                'company': line.company_id, 'savings': 0.0, 'fees': 0.0, 'fees_due': 0.0})
            row['savings'] += line.amount_savings
            row['fees'] += line.amount_fees
            if line.state == 'submitted' and line.amount_loan:
                row['repaid'] += line.amount_loan
                row['pending'] += line.amount_loan
        for member, amount in self._fees_due_on(day, fee_obligations, pending_fee_lines).items():
            key = next((k for k in rows if k[0] == member), (member, Loan))
            row = rows.setdefault(key, {
                'expected': 0.0, 'arrears_bf': 0.0, 'paid_due': 0.0, 'paid_arrears': 0.0,
                'oldest_due': None, 'repaid': 0.0, 'from_savings': 0.0, 'pending': 0.0,
                'member': member, 'loan': Loan, 'union': member.union_id,
                'company': member.union_id.company_id, 'savings': 0.0, 'fees': 0.0, 'fees_due': 0.0})
            row['fees_due'] += amount

        result = []
        for row in rows.values():
            # Cash still waiting for the manager has not been allocated yet: it clears the
            # remaining arrears first, then the day's installment, as posting will.
            arrears_left = max(0.0, row['arrears_bf'] - row['paid_arrears'])
            pending_to_arrears = min(row['pending'], arrears_left)
            pending_to_due = row['pending'] - pending_to_arrears
            met = min(row['expected'], row['paid_due'] + pending_to_due)
            shortfall = row['expected'] - met
            if currency_zero(shortfall):
                shortfall = 0.0
            row.update({
                'met': met,
                'shortfall': shortfall,
                'arrears_cf': max(0.0, arrears_left - pending_to_arrears) + shortfall,
                'days_late': (day - row['oldest_due']).days if row['oldest_due'] else 0,
                'total': row['repaid'] + row['savings'] + row['fees'],
                'phone': row['member'].phone or row['member'].mobile or '',
                'installment': row['loan'].installment_amount if row['loan'] else 0.0,
            })
            if currency_zero(row['expected']) and currency_zero(row['total']) and currency_zero(row['fees_due']):
                continue
            result.append(row)
        result.sort(key=lambda r: r['member'].name or '')
        return result

    def _day_totals(self, rows):
        totals = self._empty_day_totals()
        for row in rows:
            totals['members'] += 1
            totals['defaulters'] += 1 if row['shortfall'] else 0
            for key in ('expected', 'arrears_bf', 'repaid', 'from_savings', 'savings', 'fees', 'fees_due',
                        'total', 'met', 'shortfall', 'pending'):
                totals[key] += row[key]
        return totals

    def _collection_data(self, defaults_only=False):
        """Per day: the branch > officer > union tree of member rows, plus a per-officer summary."""
        self.ensure_one()
        days = []
        summary = {}  # officer -> list of (day, totals)
        fee_obligations = self._fee_obligations()
        pending_fee_lines = self._pending_fee_lines()
        for day in self._days():
            rows = self._day_rows(day, fee_obligations, pending_fee_lines)
            if defaults_only:
                rows = [r for r in rows if r['shortfall']]
            tree = self._tree(rows, lambda r: r['union'], self._day_totals, self._sum_into)
            for branch in tree['branches']:
                for off in branch['officers']:
                    summary.setdefault(off['officer'], []).append({'day': day, 'totals': off['totals']})
            days.append({'day': day, 'tree': tree})
        officers = []
        for officer in sorted(summary, key=lambda o: (not o, o.name or '')):
            totals = self._empty_day_totals()
            for entry in summary[officer]:
                self._sum_into(totals, entry['totals'])
            officers.append({'officer': officer, 'days': summary[officer], 'totals': totals})
        grand = self._empty_day_totals()
        for day in days:
            self._sum_into(grand, day['tree']['totals'])
        return {'days': days, 'summary': officers, 'totals': grand}

    @api.model
    def _pct(self, part, whole):
        return (part / whole * 100.0) if whole else 0.0


class ReportDailyMixin(models.AbstractModel):
    _name = 'report.ranchi_centre.daily_report_mixin'
    _description = 'Ranchi Daily Report Values'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['ranchi.daily.report.wizard'].browse(docids)
        wizard = wizard.with_context(allowed_company_ids=wizard.company_ids.ids)
        values = {
            'doc_ids': docids,
            'doc_model': 'ranchi.daily.report.wizard',
            'docs': wizard,
            'wizard': wizard,
            'currency': wizard.company_ids[:1].currency_id or self.env.company.currency_id,
            'today': fields.Date.context_today(self),
            'pct': wizard._pct,
        }
        if wizard.report_type == 'disbursement':
            values['roll'] = wizard._disbursement_data()
        else:
            values['sheet'] = wizard._collection_data(defaults_only=wizard.report_type == 'default')
        return values


class ReportDisbursementRoll(models.AbstractModel):
    _name = 'report.ranchi_centre.report_disbursement_roll'
    _inherit = 'report.ranchi_centre.daily_report_mixin'
    _description = 'Disbursement Master Roll'


class ReportDailyCollection(models.AbstractModel):
    _name = 'report.ranchi_centre.report_daily_collection'
    _inherit = 'report.ranchi_centre.daily_report_mixin'
    _description = 'Daily Collection Sheet'


class ReportDailyDefaults(models.AbstractModel):
    _name = 'report.ranchi_centre.report_daily_defaults'
    _inherit = 'report.ranchi_centre.daily_report_mixin'
    _description = 'Defaults of the Day'
