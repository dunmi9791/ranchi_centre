# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

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
    loan_type_id = fields.Many2one(
        'ranchi.loan.type', string="Loan Type", required=True, tracking=True, index=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="The one loan product this union runs. Members of the union can only apply for it.")
    meeting_frequency = fields.Selection(
        related='loan_type_id.meeting_frequency', store=True, readonly=True)
    union_day = fields.Selection(
        WEEKDAYS, string="Union Day", tracking=True,
        compute='_compute_union_day', store=True, readonly=False,
        help="Weekday the union meets. Left empty for unions that meet daily.")
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
    purse_ids = fields.One2many('ranchi.union.purse', 'union_id', string="Purses")
    purse_id = fields.Many2one('ranchi.union.purse', string="Union Purse", compute='_compute_purse')
    purse_balance = fields.Monetary(
        string="Purse Balance", compute='_compute_purse', currency_field='currency_id',
        help="Money held in the union purse: union purse fees collected plus manager entries.")

    _sql_constraints = [
        ('name_company_uniq', 'unique(name, company_id)', 'A union with this name already exists in this branch.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', _('New')) == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('ranchi.union') or '/'
        unions = super().create(vals_list)
        unions._ensure_purse()
        return unions

    def _ensure_purse(self):
        """Give every union in *self* its purse (created with the union, kept for life)."""
        missing = self.sudo().filtered(lambda u: not u.purse_ids)
        if missing:
            self.env['ranchi.union.purse'].sudo().create([{'union_id': u.id} for u in missing])
        return True

    @api.depends('purse_ids.balance')
    def _compute_purse(self):
        for union in self:
            purse = union.sudo().purse_ids[:1]
            union.purse_id = purse.id
            union.purse_balance = purse.balance

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

    @api.depends('loan_type_id')
    def _compute_union_day(self):
        for union in self:
            union.union_day = False if union.loan_type_id.meeting_frequency == 'daily' else union.union_day

    @api.constrains('union_day', 'loan_type_id')
    def _check_union_day(self):
        for union in self:
            if not union.loan_type_id:
                continue
            if union.meeting_frequency == 'daily' and union.union_day:
                raise ValidationError(_(
                    "%(union)s runs %(type)s, which meets daily: it cannot have a union day.",
                    union=union.name, type=union.loan_type_id.name))
            if union.meeting_frequency == 'weekly' and not union.union_day:
                raise ValidationError(_(
                    "%(union)s runs %(type)s, which meets weekly: choose a union day.",
                    union=union.name, type=union.loan_type_id.name))

    @api.constrains('loan_type_id', 'company_id')
    def _check_loan_type_company(self):
        for union in self:
            lt_company = union.loan_type_id.company_id
            if lt_company and lt_company != union.company_id:
                raise ValidationError(_("The loan type must be available in the union's branch."))

    def write(self, vals):
        if 'loan_type_id' in vals:
            for union in self:
                if union.loan_type_id.id == vals['loan_type_id']:
                    continue
                active = union.loan_ids.filtered(lambda l: l.state in ('applied', 'approved', 'fees', 'disbursed'))
                if active:
                    raise UserError(_(
                        "Cannot change the loan type of %(union)s while it has %(count)s active loan(s). "
                        "Close or cancel them first.", union=union.name, count=len(active)))
        return super().write(vals)

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

    def action_view_purse(self):
        self.ensure_one()
        self._ensure_purse()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Union Purse"),
            'res_model': 'ranchi.union.purse',
            'res_id': self.sudo().purse_ids[:1].id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_collections(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_collection')
        action['domain'] = [('union_id', '=', self.id)]
        action['context'] = {'default_union_id': self.id}
        return action
