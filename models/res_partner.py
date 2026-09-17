# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_ranchi_member = fields.Boolean(string="Ranchi Member", index=True)
    member_number = fields.Char(string="Member No.", copy=False, readonly=True, index=True)
    membership_state = fields.Selection(
        [('applied', 'Applied'), ('vetted', 'Vetted'), ('confirmed', 'Confirmed'), ('exited', 'Exited')],
        string="Membership Status", default='applied', tracking=True, copy=False)
    membership_date = fields.Date(string="Member Since", readonly=True, copy=False)
    exit_date = fields.Date(readonly=True, copy=False)
    union_id = fields.Many2one(
        'ranchi.union', string="Union", index=True, tracking=True, ondelete='restrict')
    credit_officer_id = fields.Many2one(
        related='union_id.credit_officer_id', string="Credit Officer", store=True, readonly=True)
    membership_fee_move_id = fields.Many2one(
        'account.move', string="Membership Fee Invoice", readonly=True, copy=False)

    # ---- KYC -------------------------------------------------------------
    nin = fields.Char(string="National ID Number (NIN)", copy=False)
    bvn = fields.Char(string="Bank Verification Number (BVN)", copy=False)
    next_of_kin = fields.Char()
    next_of_kin_relationship = fields.Char()
    next_of_kin_phone = fields.Char()
    next_of_kin_email = fields.Char()

    # ---- residence / business survey -------------------------------------
    landmark = fields.Char()
    building_type = fields.Selection(
        [('bungalow', 'Bungalow'), ('duplex', 'Duplex'), ('apartment', 'Apartment'),
         ('hotel', 'Hotel'), ('uncompleted', 'Uncompleted'), ('shop', 'Shop / Stall')])
    years_of_occupancy = fields.Integer()
    number_of_portraits = fields.Integer(help="Family portraits seen at the residence during the visit.")
    occupancy_details = fields.Text(string="Branch visit report")
    type_items = fields.Char(string="Type of items in shop")
    business_description = fields.Text()

    # ---- savings ---------------------------------------------------------
    savings_balance = fields.Monetary(
        string="Savings Balance", compute='_compute_savings_balances', currency_field='currency_id')
    savings_on_hold = fields.Monetary(
        string="Savings On Hold", compute='_compute_savings_balances', currency_field='currency_id')
    savings_available = fields.Monetary(
        string="Available Savings", compute='_compute_savings_balances', currency_field='currency_id')
    savings_transaction_ids = fields.One2many('ranchi.savings.transaction', 'member_id')
    withdrawal_request_ids = fields.One2many('ranchi.withdrawal.request', 'member_id')

    # ---- loans -----------------------------------------------------------
    loan_ids = fields.One2many('ranchi.loan', 'member_id', string="Loans")
    loan_count = fields.Integer(compute='_compute_loan_stats')
    active_loan_id = fields.Many2one('ranchi.loan', compute='_compute_loan_stats', string="Active Loan")
    loan_cycle = fields.Integer(
        compute='_compute_loan_stats', string="Loans Fully Repaid",
        help="Number of loans this member has fully repaid. Drives loan stage eligibility.")
    loans_outstanding = fields.Monetary(
        compute='_compute_loan_stats', currency_field='currency_id')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('is_ranchi_member', 'company_id')
    def _compute_savings_balances(self):
        """Balance = credit side of the company's savings liability account for this partner."""
        members = self.filtered('is_ranchi_member')
        (self - members).update({'savings_balance': 0.0, 'savings_on_hold': 0.0, 'savings_available': 0.0})
        if not members:
            return
        balances = {}
        companies = members.mapped('company_id') | self.env.company
        for company in companies:
            account = company.ranchi_savings_liability_account_id
            if not account:
                continue
            groups = self.env['account.move.line'].sudo()._read_group(
                [('account_id', '=', account.id),
                 ('partner_id', 'in', members.ids),
                 ('parent_state', '=', 'posted'),
                 ('company_id', '=', company.id)],
                ['partner_id'], ['balance:sum'])
            for partner, balance in groups:
                balances[partner.id] = balances.get(partner.id, 0.0) - balance
        holds = {}
        hold_groups = self.env['ranchi.savings.transaction'].sudo()._read_group(
            [('member_id', 'in', members.ids), ('type', '=', 'hold'), ('state', '=', 'posted')],
            ['member_id'], ['amount:sum'])
        for partner, amount in hold_groups:
            holds[partner.id] = amount
        for member in members:
            member.savings_balance = balances.get(member.id, 0.0)
            member.savings_on_hold = holds.get(member.id, 0.0)
            member.savings_available = member.savings_balance - member.savings_on_hold

    @api.depends('loan_ids.state', 'loan_ids.balance')
    def _compute_loan_stats(self):
        for partner in self:
            loans = partner.loan_ids
            partner.loan_count = len(loans)
            active = loans.filtered(lambda l: l.state in ('applied', 'approved', 'fees', 'disbursed'))
            partner.active_loan_id = active[:1]
            partner.loan_cycle = len(loans.filtered(lambda l: l.state == 'paid'))
            partner.loans_outstanding = sum(active.mapped('balance'))

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('is_ranchi_member', 'nin', 'bvn', 'membership_state')
    def _check_member_identity(self):
        for partner in self:
            if partner.is_ranchi_member and partner.membership_state != 'applied' \
                    and not partner.nin and not partner.bvn:
                raise ValidationError(_(
                    "%s cannot be vetted or confirmed without a NIN or a BVN.", partner.display_name))

    @api.constrains('is_ranchi_member', 'union_id', 'company_id')
    def _check_member_union(self):
        for partner in self:
            if partner.is_ranchi_member and partner.union_id and partner.company_id \
                    and partner.union_id.company_id != partner.company_id:
                raise ValidationError(_("The member's branch must match the union's branch."))

    @api.constrains('member_number')
    def _check_member_number_unique(self):
        for partner in self.filtered('member_number'):
            dup = self.search_count([('member_number', '=', partner.member_number), ('id', '!=', partner.id)])
            if dup:
                raise ValidationError(_("Member number %s is already used.", partner.member_number))

    # ------------------------------------------------------------------
    # Membership pipeline
    # ------------------------------------------------------------------
    def _ranchi_company(self):
        self.ensure_one()
        return self.company_id or self.union_id.company_id or self.env.company

    def action_open_membership_fee_wizard(self):
        self.ensure_one()
        if not self.is_ranchi_member:
            raise UserError(_("Mark the contact as a Ranchi member first."))
        if self.membership_state != 'applied':
            raise UserError(_("The membership fee is collected while the member is in 'Applied'."))
        company = self._ranchi_company()
        return {
            'name': _('Collect Membership Fee'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.membership.fee.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_amount': company.ranchi_membership_fee,
                'default_company_id': company.id,
            },
        }

    def _on_membership_fee_paid(self):
        for partner in self:
            company = partner._ranchi_company()
            if company.ranchi_auto_vet_on_fee and partner.membership_state == 'applied':
                partner.action_vet_member()

    def action_vet_member(self):
        for partner in self:
            if partner.membership_state != 'applied':
                raise UserError(_("Only members in 'Applied' can be vetted."))
            if not partner.union_id:
                raise UserError(_("Assign %s to a union before vetting.", partner.display_name))
        self.write({'membership_state': 'vetted'})

    def action_confirm_member(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if partner.membership_state != 'vetted':
                raise UserError(_("Only vetted members can be confirmed."))
            if not partner.member_number:
                partner.member_number = partner._generate_member_number()
            partner.write({
                'membership_state': 'confirmed',
                'membership_date': today,
                'is_ranchi_member': True,
            })

    def action_exit_member(self):
        for partner in self:
            if partner.membership_state != 'confirmed':
                raise UserError(_("Only confirmed members can exit."))
            if partner.active_loan_id:
                raise UserError(_("%s still has an active loan.", partner.display_name))
            if partner.savings_balance:
                raise UserError(_("%s still has a savings balance. Pay it out first.", partner.display_name))
        self.write({'membership_state': 'exited', 'exit_date': fields.Date.context_today(self)})

    def action_reset_to_applied(self):
        for partner in self:
            if partner.membership_state not in ('vetted', 'exited'):
                raise UserError(_("Only vetted or exited members can be reset to 'Applied'."))
        self.write({'membership_state': 'applied'})

    def _generate_member_number(self):
        self.ensure_one()
        company = self._ranchi_company()
        code = company.ranchi_branch_code or ''.join(
            word[0] for word in company.name.split()[:3]).upper()
        seq = self.env['ir.sequence'].next_by_code('ranchi.member') or '/'
        return f"{code}/{seq}"

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_view_loans(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_loan')
        action['domain'] = [('member_id', '=', self.id)]
        action['context'] = {'default_member_id': self.id, 'default_union_id': self.union_id.id}
        return action

    def action_view_savings_transactions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_savings_transaction')
        action['domain'] = [('member_id', '=', self.id)]
        action['context'] = {'default_member_id': self.id, 'search_default_posted': 1}
        return action

    def action_view_withdrawal_requests(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('ranchi_centre.action_ranchi_withdrawal_request')
        action['domain'] = [('member_id', '=', self.id)]
        action['context'] = {'default_member_id': self.id}
        return action

    def action_print_savings_statement(self):
        self.ensure_one()
        return {
            'name': _('Savings Statement'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.savings.statement.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_member_id': self.id},
        }
