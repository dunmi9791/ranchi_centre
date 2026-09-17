# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RanchiLoanType(models.Model):
    _name = 'ranchi.loan.type'
    _description = 'Ranchi Loan Type'
    _order = 'sequence, name'
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    code = fields.Char(size=16)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="Branch",
        help="Leave empty to make this loan type available to every branch.")
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_currency_id')
    description = fields.Text()

    service_rate = fields.Float(
        string="Service Charge (%)", required=True, default=0.0, digits=(5, 2),
        help="Flat percentage of the principal charged once over the life of the loan.")
    service_collection = fields.Selection(
        [('spread', 'Spread over installments'), ('upfront', 'Collected with the fees, before disbursement')],
        string="Service Charge Collection", default='spread', required=True)
    admin_charge = fields.Monetary(
        string="Administration Fee", currency_field='currency_id', default=0.0,
        help="Flat fee invoiced before disbursement.")
    risk_premium_rate = fields.Float(
        string="Risk Premium (%)", default=0.0, digits=(5, 2),
        help="Percentage of the approved amount invoiced before disbursement.")

    installment_count = fields.Integer(string="Number of Installments", required=True, default=20)
    installment_period = fields.Selection(
        [('daily', 'Daily'), ('weekly', 'Weekly'), ('biweekly', 'Every two weeks'), ('monthly', 'Monthly')],
        required=True, default='weekly')
    grace_days = fields.Integer(
        string="Grace Days", default=0,
        help="Days after the due date before an installment is treated as overdue.")
    min_amount = fields.Monetary(currency_field='currency_id', default=0.0)
    max_amount = fields.Monetary(currency_field='currency_id', default=0.0,
                                 help="0 means no ceiling at loan type level.")
    stage_ids = fields.One2many('ranchi.loan.stage', 'loan_type_id', string="Stages")
    loan_count = fields.Integer(compute='_compute_loan_count')

    @api.depends('company_id')
    def _compute_currency_id(self):
        for rec in self:
            rec.currency_id = (rec.company_id or self.env.company).currency_id

    def _compute_loan_count(self):
        groups = self.env['ranchi.loan']._read_group(
            [('loan_type_id', 'in', self.ids)], ['loan_type_id'], ['__count'])
        counts = {lt.id: count for lt, count in groups}
        for rec in self:
            rec.loan_count = counts.get(rec.id, 0)

    @api.constrains('installment_count', 'service_rate', 'risk_premium_rate', 'min_amount', 'max_amount')
    def _check_values(self):
        for rec in self:
            if rec.installment_count <= 0:
                raise ValidationError(_("The number of installments must be greater than zero."))
            if rec.service_rate < 0 or rec.risk_premium_rate < 0:
                raise ValidationError(_("Rates cannot be negative."))
            if rec.max_amount and rec.min_amount > rec.max_amount:
                raise ValidationError(_("The minimum amount cannot exceed the maximum amount."))

    def _period_delta(self):
        """Return a relativedelta for one installment period."""
        from dateutil.relativedelta import relativedelta
        self.ensure_one()
        return {
            'daily': relativedelta(days=1),
            'weekly': relativedelta(weeks=1),
            'biweekly': relativedelta(weeks=2),
            'monthly': relativedelta(months=1),
        }[self.installment_period]


class RanchiLoanStage(models.Model):
    _name = 'ranchi.loan.stage'
    _description = 'Ranchi Loan Stage (cycle ladder)'
    _order = 'loan_type_id, sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    loan_type_id = fields.Many2one('ranchi.loan.type', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='loan_type_id.currency_id')
    max_principal = fields.Monetary(
        string="Maximum Principal", currency_field='currency_id', required=True)
    min_cycle = fields.Integer(
        string="Loans Repaid Required", default=0,
        help="How many loans the member must have fully repaid to be eligible for this stage.")

    @api.constrains('max_principal', 'min_cycle')
    def _check_values(self):
        for rec in self:
            if rec.max_principal <= 0:
                raise ValidationError(_("The maximum principal must be positive."))
            if rec.min_cycle < 0:
                raise ValidationError(_("The required loan cycle cannot be negative."))
