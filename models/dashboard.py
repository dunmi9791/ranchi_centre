# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class RanchiDashboard(models.AbstractModel):
    """Figures for the Ranchi Centre dashboard.

    Everything is read as the current user, so record rules apply: a credit officer only
    sees their own unions, while accountants, managers and auditors see the whole branch
    (or every branch selected in the company switcher).
    """
    _name = 'ranchi.dashboard'
    _description = 'Ranchi Centre Dashboard'

    # Actions the client may open from a KPI card, with the domain that matches the figure.
    _ACTIONS = {
        'unions': ('ranchi_centre.action_ranchi_union', [], {}),
        'unions_today': ('ranchi_centre.action_ranchi_union', 'meeting_today', {}),
        'members': ('ranchi_centre.action_ranchi_members', [], {}),
        'members_pipeline': ('ranchi_centre.action_ranchi_members',
                             [('membership_state', 'in', ('applied', 'vetted'))], {'search_default_all': 1}),
        'loans_active': ('ranchi_centre.action_ranchi_loan', [('state', '=', 'disbursed')], {}),
        'loans_pending': ('ranchi_centre.action_ranchi_loan', [('state', '=', 'applied')], {}),
        'loans_awaiting': ('ranchi_centre.action_ranchi_loan', [('state', 'in', ('approved', 'fees'))], {}),
        'installments_overdue': ('ranchi_centre.action_ranchi_loan_installment', [('status', '=', 'overdue')], {}),
        'installments_today': ('ranchi_centre.action_ranchi_loan_installment', [('status', '=', 'due')], {}),
        'savings': ('ranchi_centre.action_ranchi_savings_transaction', [('state', '=', 'posted')], {}),
        'withdrawals_pending': ('ranchi_centre.action_ranchi_withdrawal_request',
                                [('state', 'in', ('submitted', 'approved_l1', 'approved'))], {}),
        'collections_today': ('ranchi_centre.action_ranchi_collection', 'today', {}),
        'collections_draft': ('ranchi_centre.action_ranchi_collection', [('state', '=', 'draft')], {}),
        'disbursements_pending': ('ranchi_centre.action_ranchi_disbursement',
                                  [('state', 'in', ('pending', 'processing'))], {}),
        'disbursements_failed': ('ranchi_centre.action_ranchi_disbursement', [('state', '=', 'failed')], {}),
    }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @api.model
    def _check_access(self):
        if not (self.env.user.has_group('ranchi_centre.group_ranchi_officer')
                or self.env.user.has_group('ranchi_centre.group_ranchi_auditor')):
            raise AccessError(_("You do not have access to the Ranchi Centre dashboard."))

    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    @api.model
    def _meeting_today_domain(self, today=None):
        today = today or self._today()
        return ['|', ('meeting_frequency', '=', 'daily'),
                '&', ('meeting_frequency', '=', 'weekly'), ('union_day', '=', str(today.weekday()))]

    @api.model
    def _member_domain(self, unions):
        """Members the user works with: partners carry no union record rule, so scope them
        through the unions the user can read, exactly like the officer API does."""
        return [('is_ranchi_member', '=', True), ('union_id', 'in', unions.ids)]

    @api.model
    def _sum(self, model, domain, field):
        groups = self.env[model]._read_group(domain, [], [f'{field}:sum'])
        return (groups[0][0] if groups else 0.0) or 0.0

    @api.model
    def _savings_total(self, members):
        """Credit balance of the savings liability account for these members, per company,
        the same source of truth as ``res.partner.savings_balance``."""
        total = 0.0
        if not members:
            return total
        for company in self.env.companies:
            account = company.ranchi_savings_liability_account_id
            if not account:
                continue
            groups = self.env['account.move.line'].sudo()._read_group(
                [('account_id', '=', account.id), ('partner_id', 'in', members.ids),
                 ('parent_state', '=', 'posted'), ('company_id', '=', company.id)],
                [], ['balance:sum'])
            if groups:
                total -= groups[0][0] or 0.0
        return total

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------
    @api.model
    def get_data(self):
        self._check_access()
        today = self._today()
        month_start = today.replace(day=1)
        Union = self.env['ranchi.union']
        Partner = self.env['res.partner']
        Loan = self.env['ranchi.loan']
        Installment = self.env['ranchi.loan.installment']
        Withdrawal = self.env['ranchi.withdrawal.request']
        Collection = self.env['ranchi.collection']
        Disbursement = self.env['ranchi.disbursement']

        unions = Union.search([])
        active_unions = unions.filtered(lambda u: u.state == 'active')
        member_domain = self._member_domain(unions)
        members = Partner.search(member_domain)
        confirmed = members.filtered(lambda m: m.membership_state == 'confirmed')

        active_loans = Loan.search([('state', '=', 'disbursed')])
        outstanding = sum(active_loans.mapped('balance'))
        overdue_domain = [('status', '=', 'overdue')]
        overdue_residual = self._sum('ranchi.loan.installment', overdue_domain, 'amount_residual')
        overdue_count = Installment.search_count(overdue_domain)
        overdue_loans = Installment._read_group(overdue_domain, ['loan_id'], ['__count'])
        due_today_domain = [('status', '=', 'due')]

        posted_savings = [('state', '=', 'posted')]
        month_savings = posted_savings + [('date', '>=', month_start), ('date', '<=', today)]
        pending_withdrawals = Withdrawal.search([('state', 'in', ('submitted', 'approved_l1', 'approved'))])
        pending_disbursements = Disbursement.search([('state', 'in', ('pending', 'processing'))])

        collections_today = Collection.search([('date', '=', today), ('state', '!=', 'cancelled')])
        collections_month = self._sum(
            'ranchi.collection', [('state', '=', 'posted'), ('date', '>=', month_start), ('date', '<=', today)],
            'amount_total')

        currency = self.env.company.currency_id
        return {
            'today': fields.Date.to_string(today),
            'company': self.env.company.name,
            'currency': {
                'symbol': currency.symbol, 'position': currency.position,
                'decimals': currency.decimal_places,
            },
            'unions': {
                'total': len(unions),
                'active': len(active_unions),
                'meeting_today': len(unions.filtered_domain(self._meeting_today_domain(today))),
                'by_loan_type': [
                    {'name': lt.name, 'count': len(recs), 'frequency': lt.meeting_frequency}
                    for lt, recs in unions.grouped('loan_type_id').items() if lt
                ],
            },
            'members': {
                'total': len(members),
                'confirmed': len(confirmed),
                'applied': len(members.filtered(lambda m: m.membership_state == 'applied')),
                'vetted': len(members.filtered(lambda m: m.membership_state == 'vetted')),
                'new_this_month': Partner.search_count(
                    member_domain + [('membership_date', '>=', month_start), ('membership_date', '<=', today)]),
                'with_active_loan': len(active_loans.mapped('member_id')),
            },
            'loans': {
                'active': len(active_loans),
                'outstanding': outstanding,
                'pending_approval': Loan.search_count([('state', '=', 'applied')]),
                'awaiting_disbursement': Loan.search_count([('state', 'in', ('approved', 'fees'))]),
                'disbursed_this_month': Loan.search_count(
                    [('date_disbursed', '>=', month_start), ('date_disbursed', '<=', today)]),
                'disbursed_this_month_amount': self._sum(
                    'ranchi.loan', [('date_disbursed', '>=', month_start), ('date_disbursed', '<=', today)],
                    'amount_approved'),
                'overdue_amount': overdue_residual,
                'overdue_installments': overdue_count,
                'overdue_loans': len(overdue_loans),
                'par': (overdue_residual / outstanding * 100.0) if outstanding else 0.0,
                'due_today': Installment.search_count(due_today_domain),
                'due_today_amount': self._sum('ranchi.loan.installment', due_today_domain, 'amount_residual'),
            },
            'savings': {
                'total': self._savings_total(members),
                'deposits_this_month': self._sum(
                    'ranchi.savings.transaction', month_savings + [('type', '=', 'deposit')], 'amount'),
                'withdrawals_this_month': self._sum(
                    'ranchi.savings.transaction', month_savings + [('type', '=', 'withdrawal')], 'amount'),
                'pending_withdrawals': len(pending_withdrawals),
                'pending_withdrawals_amount': sum(pending_withdrawals.mapped('amount')),
            },
            'collections': {
                'today': len(collections_today),
                'today_amount': sum(collections_today.mapped('amount_total')),
                'draft': Collection.search_count([('state', '=', 'draft')]),
                'this_month': collections_month,
            },
            'disbursements': {
                'pending': len(pending_disbursements),
                'pending_amount': sum(pending_disbursements.mapped('amount')),
                'failed': Disbursement.search_count([('state', '=', 'failed')]),
            },
            'top_overdue_unions': self._top_overdue_unions(),
            'monthly': self._monthly_series(today),
        }

    @api.model
    def _top_overdue_unions(self, limit=5):
        groups = self.env['ranchi.loan.installment']._read_group(
            [('status', '=', 'overdue')], ['union_id'], ['amount_residual:sum', '__count'],
            order='amount_residual:sum desc', limit=limit)
        return [
            {'id': union.id, 'name': union.display_name, 'amount': amount, 'count': count}
            for union, amount, count in groups if union
        ]

    @api.model
    def _monthly_series(self, today, months=6):
        """Disbursed vs collected (loan repayments + savings deposits) per month, oldest first."""
        first = today.replace(day=1) - relativedelta(months=months - 1)

        def by_month(model, domain, date_field, amount_field):
            # A ``:month`` groupby returns the first day of each month as a date.
            return {
                (month.year, month.month): amount or 0.0
                for month, amount in self.env[model]._read_group(
                    domain + [(date_field, '>=', first), (date_field, '<=', today)],
                    [f'{date_field}:month'], [f'{amount_field}:sum'])
                if month
            }

        disbursed = by_month('ranchi.loan', [('state', 'in', ('disbursed', 'paid', 'written_off'))],
                             'date_disbursed', 'amount_approved')
        repaid = by_month('ranchi.loan.repayment', [], 'date', 'amount')
        deposits = by_month('ranchi.savings.transaction', [('state', '=', 'posted'), ('type', '=', 'deposit')],
                            'date', 'amount')
        out = []
        for i in range(months):
            month = first + relativedelta(months=i)
            key = (month.year, month.month)
            out.append({
                'label': month.strftime('%b %y'),
                'disbursed': disbursed.get(key, 0.0),
                'repaid': repaid.get(key, 0.0),
                'deposits': deposits.get(key, 0.0),
            })
        return out

    @api.model
    def open_action(self, key):
        self._check_access()
        xml_id, domain, context = self._ACTIONS[key]
        action = self.env['ir.actions.act_window']._for_xml_id(xml_id)
        if domain == 'meeting_today':
            domain = self._meeting_today_domain()
        elif domain == 'today':
            domain = [('date', '=', self._today())]
        action['domain'] = domain
        action['context'] = dict(context)
        return action
