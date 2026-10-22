# -*- coding: utf-8 -*-
from odoo import api, fields, models, tools, _

PAR_BUCKETS = [
    ('current', 'Current'),
    ('1_30', '1-30 Days'),
    ('31_60', '31-60 Days'),
    ('61_90', '61-90 Days'),
    ('90_plus', 'Over 90 Days'),
    ('paid', 'Fully Paid'),
    ('written_off', 'Written Off'),
]


class RanchiPortfolioReport(models.Model):
    """One row per loan that reached disbursement, with its arrears as of today.

    An installment is in arrears once it is unpaid past its due date plus the loan type's
    grace days, the same rule as ``ranchi.loan.installment.status``. A loan's days in
    arrears count from its oldest installment in arrears; portfolio at risk (PAR) is the
    whole outstanding balance of the loans in arrears, not only the overdue part.
    """
    _name = 'ranchi.portfolio.report'
    _description = 'Ranchi Portfolio Analysis'
    _auto = False
    _order = 'company_id, credit_officer_id, union_id, days_in_arrears desc, loan_id'
    _rec_name = 'loan_id'

    loan_id = fields.Many2one('ranchi.loan', string="Loan", readonly=True)
    company_id = fields.Many2one('res.company', string="Branch", readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    credit_officer_id = fields.Many2one('hr.employee', string="Credit Officer", readonly=True)
    credit_officer_user_id = fields.Many2one('res.users', string="Credit Officer User", readonly=True)
    union_id = fields.Many2one('ranchi.union', string="Union", readonly=True)
    loan_type_id = fields.Many2one('ranchi.loan.type', string="Loan Type", readonly=True)
    member_id = fields.Many2one('res.partner', string="Member", readonly=True)
    state = fields.Selection(
        [('disbursed', 'Disbursed'), ('paid', 'Fully Paid'), ('written_off', 'Written Off')],
        readonly=True)
    date_disbursed = fields.Date(string="Disbursed On", readonly=True)
    date_closed = fields.Date(string="Closed On", readonly=True)
    oldest_overdue_date = fields.Date(string="Oldest Overdue Due Date", readonly=True)

    amount_disbursed = fields.Monetary(string="Principal", readonly=True)
    amount_repayable = fields.Monetary(string="Total Repayable", readonly=True)
    amount_repaid = fields.Monetary(string="Repaid", readonly=True)
    amount_written_off = fields.Monetary(string="Written Off Amount", readonly=True)
    balance = fields.Monetary(string="Outstanding", readonly=True)
    amount_overdue = fields.Monetary(string="Arrears", readonly=True, help="Unpaid amount of installments in arrears.")
    overdue_count = fields.Integer(string="Installments in Arrears", readonly=True)
    days_in_arrears = fields.Integer(string="Days in Arrears", readonly=True, aggregator='max')
    par_bucket = fields.Selection(PAR_BUCKETS, string="Arrears Bucket", readonly=True)
    active_count = fields.Integer(string="Active Loans", readonly=True)
    arrears_count = fields.Integer(string="Loans in Arrears", readonly=True)
    written_off_count = fields.Integer(string="Loans Written Off", readonly=True)
    par1_balance = fields.Monetary(string="PAR > 0 Days", readonly=True)
    par30_balance = fields.Monetary(string="PAR > 30 Days", readonly=True)
    par90_balance = fields.Monetary(string="PAR > 90 Days", readonly=True)

    @property
    def _table_query(self):
        return """
            SELECT p.*,
                   CASE WHEN p.state = 'paid' THEN 'paid'
                        WHEN p.state = 'written_off' THEN 'written_off'
                        WHEN p.days_in_arrears = 0 THEN 'current'
                        WHEN p.days_in_arrears <= 30 THEN '1_30'
                        WHEN p.days_in_arrears <= 60 THEN '31_60'
                        WHEN p.days_in_arrears <= 90 THEN '61_90'
                        ELSE '90_plus' END AS par_bucket,
                   CASE WHEN p.state = 'disbursed' THEN 1 ELSE 0 END AS active_count,
                   CASE WHEN p.days_in_arrears > 0 THEN 1 ELSE 0 END AS arrears_count,
                   CASE WHEN p.state = 'written_off' THEN 1 ELSE 0 END AS written_off_count,
                   CASE WHEN p.days_in_arrears > 0 THEN p.balance ELSE 0 END AS par1_balance,
                   CASE WHEN p.days_in_arrears > 30 THEN p.balance ELSE 0 END AS par30_balance,
                   CASE WHEN p.days_in_arrears > 90 THEN p.balance ELSE 0 END AS par90_balance
              FROM (
                SELECT l.id AS id,
                       l.id AS loan_id,
                       l.company_id,
                       u.credit_officer_id,
                       u.credit_officer_user_id,
                       l.union_id,
                       l.loan_type_id,
                       l.member_id,
                       l.state,
                       l.date_disbursed,
                       l.date_closed,
                       COALESCE(l.amount_approved, 0) AS amount_disbursed,
                       COALESCE(i.amount_repayable, 0) AS amount_repayable,
                       COALESCE(r.amount_repaid, 0) AS amount_repaid,
                       COALESCE(r.amount_written_off, 0) AS amount_written_off,
                       CASE WHEN l.state = 'disbursed' THEN COALESCE(i.balance, 0) ELSE 0 END AS balance,
                       CASE WHEN l.state = 'disbursed' THEN COALESCE(i.amount_overdue, 0) ELSE 0 END AS amount_overdue,
                       CASE WHEN l.state = 'disbursed' THEN COALESCE(i.overdue_count, 0) ELSE 0 END AS overdue_count,
                       CASE WHEN l.state = 'disbursed' THEN i.oldest_overdue END AS oldest_overdue_date,
                       CASE WHEN l.state = 'disbursed' AND i.oldest_overdue IS NOT NULL
                            THEN CURRENT_DATE - i.oldest_overdue ELSE 0 END AS days_in_arrears
                  FROM ranchi_loan l
                  JOIN ranchi_loan_type lt ON lt.id = l.loan_type_id
             LEFT JOIN ranchi_union u ON u.id = l.union_id
             LEFT JOIN LATERAL (
                    SELECT SUM(inst.amount_total) AS amount_repayable,
                           SUM(inst.amount_residual) AS balance,
                           SUM(inst.amount_residual) FILTER (WHERE inst.state != 'paid'
                               AND inst.date_due < CURRENT_DATE - COALESCE(lt.grace_days, 0)) AS amount_overdue,
                           COUNT(*) FILTER (WHERE inst.state != 'paid'
                               AND inst.date_due < CURRENT_DATE - COALESCE(lt.grace_days, 0)) AS overdue_count,
                           MIN(inst.date_due) FILTER (WHERE inst.state != 'paid'
                               AND inst.date_due < CURRENT_DATE - COALESCE(lt.grace_days, 0)) AS oldest_overdue
                      FROM ranchi_loan_installment inst
                     WHERE inst.loan_id = l.id
                  ) i ON TRUE
             LEFT JOIN LATERAL (
                    SELECT SUM(rep.amount) FILTER (WHERE rep.source != 'writeoff') AS amount_repaid,
                           SUM(rep.amount) FILTER (WHERE rep.source = 'writeoff') AS amount_written_off
                      FROM ranchi_loan_repayment rep
                     WHERE rep.loan_id = l.id
                  ) r ON TRUE
                 WHERE l.state IN ('disbursed', 'paid', 'written_off')
              ) p
        """

    # ------------------------------------------------------------------
    # PDF report data
    # ------------------------------------------------------------------
    @api.model
    def _empty_totals(self):
        return {
            'unions': 0, 'members': 0, 'active_loans': 0, 'principal': 0.0, 'balance': 0.0,
            'overdue': 0.0, 'arrears_loans': 0, 'par1': 0.0, 'par30': 0.0, 'par90': 0.0,
            'written_off_loans': 0, 'written_off': 0.0,
        }

    @api.model
    def _add_totals(self, totals, other):
        for key, value in other.items():
            totals[key] += value
        return totals

    @api.model
    def _row_totals(self, rows):
        active = rows.filtered(lambda r: r.state == 'disbursed')
        written_off = rows.filtered(lambda r: r.state == 'written_off')
        return {
            'unions': 0, 'members': 0,
            'active_loans': len(active),
            'principal': sum(active.mapped('amount_disbursed')),
            'balance': sum(active.mapped('balance')),
            'overdue': sum(active.mapped('amount_overdue')),
            'arrears_loans': sum(active.mapped('arrears_count')),
            'par1': sum(active.mapped('par1_balance')),
            'par30': sum(active.mapped('par30_balance')),
            'par90': sum(active.mapped('par90_balance')),
            'written_off_loans': len(written_off),
            'written_off': sum(written_off.mapped('amount_written_off')),
        }

    @api.model
    def _portfolio_data(self, companies, officers=None, loan_types=None):
        """Branch > credit officer > union tree with loan totals, as the current user sees it.

        Every union of the selected branches is listed, also those without loans, so an
        officer's report shows the whole round. Unions without a credit officer are grouped
        under an empty officer.
        """
        union_domain = [('company_id', 'in', companies.ids)]
        if officers:
            union_domain.append(('credit_officer_id', 'in', officers.ids))
        if loan_types:
            union_domain.append(('loan_type_id', 'in', loan_types.ids))
        unions = self.env['ranchi.union'].search(union_domain, order='name')

        member_counts = dict(self.env['res.partner']._read_group(
            [('is_ranchi_member', '=', True), ('membership_state', '=', 'confirmed'),
             ('union_id', 'in', unions.ids)],
            ['union_id'], ['__count']))
        rows_by_union = self.search([('union_id', 'in', unions.ids)]).grouped('union_id')
        Officer = self.env['hr.employee']
        Rows = self.browse()

        branches = []
        for company in companies.sorted('name'):
            company_unions = unions.filtered(lambda u: u.company_id == company)
            if not company_unions:
                continue
            branch_totals = self._empty_totals()
            officer_list = []
            by_officer = company_unions.grouped('credit_officer_id')
            for officer in sorted(by_officer, key=lambda o: (not o, o.name or '')):
                officer_totals = self._empty_totals()
                union_list = []
                for union in by_officer[officer]:
                    rows = rows_by_union.get(union, Rows)
                    totals = self._row_totals(rows)
                    totals['unions'] = 1
                    totals['members'] = member_counts.get(union, 0)
                    union_list.append({
                        'union': union,
                        'totals': totals,
                        'loans': rows.filtered(lambda r: r.state == 'disbursed').sorted(
                            lambda r: (-r.days_in_arrears, r.member_id.name or '')),
                        'written_off_loans': rows.filtered(lambda r: r.state == 'written_off').sorted(
                            lambda r: r.date_closed or fields.Date.today(), reverse=True),
                    })
                    self._add_totals(officer_totals, totals)
                officer_list.append({'officer': officer or Officer, 'totals': officer_totals, 'unions': union_list})
                self._add_totals(branch_totals, officer_totals)
            branches.append({'company': company, 'totals': branch_totals, 'officers': officer_list})

        grand = self._empty_totals()
        for branch in branches:
            self._add_totals(grand, branch['totals'])
        return {'branches': branches, 'totals': grand}

    @api.model
    def _pct(self, part, whole):
        return (part / whole * 100.0) if whole else 0.0


class RanchiPortfolioReportWizard(models.TransientModel):
    _name = 'ranchi.portfolio.report.wizard'
    _description = 'Ranchi Portfolio Report'

    report_type = fields.Selection(
        [('officer', 'Credit Officer Portfolio'), ('risk', 'Portfolio at Risk Summary')],
        required=True, default='officer',
        help="Credit Officer Portfolio: one section per officer with every union and its active loans.\n"
             "Portfolio at Risk Summary: loans, arrears and PAR by branch, credit officer and union.")
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
    include_loans = fields.Boolean(
        string="List Loans", default=True,
        help="List each union's active loans and write-offs under its totals.")

    def action_print(self):
        self.ensure_one()
        xml_id = {
            'officer': 'ranchi_centre.action_report_officer_portfolio',
            'risk': 'ranchi_centre.action_report_portfolio_risk',
        }[self.report_type]
        return self.env.ref(xml_id).report_action(self)

    def action_open_analysis(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_portfolio_report')
        domain = [('company_id', 'in', self.company_ids.ids)]
        if self.credit_officer_ids:
            domain.append(('credit_officer_id', 'in', self.credit_officer_ids.ids))
        if self.loan_type_ids:
            domain.append(('loan_type_id', 'in', self.loan_type_ids.ids))
        action['domain'] = domain
        return action


class ReportPortfolioMixin(models.AbstractModel):
    _name = 'report.ranchi_centre.portfolio_mixin'
    _description = 'Ranchi Portfolio Report Values'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.env['ranchi.portfolio.report.wizard'].browse(docids)
        Report = self.env['ranchi.portfolio.report'].with_context(allowed_company_ids=wizard.company_ids.ids)
        portfolio = Report._portfolio_data(
            wizard.company_ids, officers=wizard.credit_officer_ids, loan_types=wizard.loan_type_ids)
        return {
            'doc_ids': docids,
            'doc_model': 'ranchi.portfolio.report.wizard',
            'docs': wizard,
            'wizard': wizard,
            'portfolio': portfolio,
            'currency': wizard.company_ids[:1].currency_id or self.env.company.currency_id,
            'today': fields.Date.context_today(self),
            'pct': Report._pct,
        }


class ReportOfficerPortfolio(models.AbstractModel):
    _name = 'report.ranchi_centre.report_officer_portfolio'
    _inherit = 'report.ranchi_centre.portfolio_mixin'
    _description = 'Credit Officer Portfolio Report'


class ReportPortfolioRisk(models.AbstractModel):
    _name = 'report.ranchi_centre.report_portfolio_risk'
    _inherit = 'report.ranchi_centre.portfolio_mixin'
    _description = 'Portfolio at Risk Report'
