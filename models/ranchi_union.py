# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

WEEKDAYS = [
    ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'), ('3', 'Thursday'),
    ('4', 'Friday'), ('5', 'Saturday'), ('6', 'Sunday'),
]


class RanchiUnion(models.Model):
    _name = 'ranchi.union'
    _description = 'Ranchi Union'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(readonly=True, copy=False, default=lambda self: _('New'))
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string="Branch", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    credit_officer_id = fields.Many2one(
        'hr.employee', string="Credit Officer", tracking=True, check_company=True,
        help="Employee who visits this union on union day and collects repayments and savings.")
    credit_officer_user_id = fields.Many2one(
        'res.users', string="Credit Officer User", related='credit_officer_id.user_id',
        store=True, readonly=True,
        help="Used by record rules so the officer only sees their own unions.")
    union_day = fields.Selection(WEEKDAYS, string="Union Day", tracking=True)
    meeting_venue = fields.Char()
    description = fields.Text()
    state = fields.Selection(
        [('active', 'Active'), ('dormant', 'Dormant'), ('closed', 'Closed')],
        default='active', required=True, tracking=True)

    member_ids = fields.One2many('res.partner', 'union_id', string="Members",
                                 domain=[('is_ranchi_member', '=', True)])
    member_count = fields.Integer(compute='_compute_counts')
    confirmed_member_count = fields.Integer(compute='_compute_counts')
    loan_ids = fields.One2many('ranchi.loan', 'union_id', string="Loans")
    active_loan_count = fields.Integer(compute='_compute_counts')
    loans_outstanding = fields.Monetary(compute='_compute_financials', currency_field='currency_id')
    savings_total = fields.Monetary(compute='_compute_financials', currency_field='currency_id')
    collection_ids = fields.One2many('ranchi.collection', 'union_id', string="Collections")

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'A union with this name already exists in this branch.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', _('New')) == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ranchi.union') or '/'
        return super().create(vals_list)

    @api.depends('member_ids', 'member_ids.membership_state', 'loan_ids.state')
    def _compute_counts(self):
        for union in self:
            union.member_count = len(union.member_ids)
            union.confirmed_member_count = len(
                union.member_ids.filtered(lambda m: m.membership_state == 'confirmed'))
            union.active_loan_count = len(union.loan_ids.filtered(lambda l: l.state == 'disbursed'))

    @api.depends('loan_ids.balance', 'member_ids')
    def _compute_financials(self):
        for union in self:
            union.loans_outstanding = sum(
                union.loan_ids.filtered(lambda l: l.state == 'disbursed').mapped('balance'))
            union.savings_total = sum(union.member_ids.mapped('savings_balance'))

    @api.constrains('credit_officer_id', 'company_id')
    def _check_officer_company(self):
        for union in self:
            officer = union.credit_officer_id
            if officer and officer.company_id and officer.company_id != union.company_id:
                raise ValidationError(_("The credit officer must belong to the same branch as the union."))

    def action_view_members(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_members')
        action['domain'] = [('union_id', '=', self.id)]
        action['context'] = {'default_union_id': self.id, 'default_is_ranchi_member': True,
                             'default_company_id': self.company_id.id}
        return action

    def action_view_loans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_loan')
        action['domain'] = [('union_id', '=', self.id)]
        action['context'] = {'default_union_id': self.id}
        return action

    def action_view_collections(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_collection')
        action['domain'] = [('union_id', '=', self.id)]
        action['context'] = {'default_union_id': self.id}
        return action
