# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

# Keep in sync with BUILDING_TYPES in ranchi_officer/src/lib/member.ts (mobile + web officer apps).
BUILDING_TYPES = [
    ('face_me', 'Face-me-I-face-you'),
    ('self_contained', 'Self-contained'),
    ('apartment', 'Flat / Apartment'),
    ('bungalow', 'Bungalow'),
    ('duplex', 'Duplex'),
    ('shop', 'Shop / Market stall'),
    ('hotel', 'Hotel'),
    ('uncompleted', 'Uncompleted building'),
    ('other', 'Other'),
]
# Labels older app builds sent before the apps used the keys above.
_BUILDING_TYPE_ALIASES = {
    'flat': 'apartment', 'shop or market stall': 'shop', 'shop attached': 'shop', 'shop / stall': 'shop',
}


def normalize_building_type(value):
    """Map an API value (key, label or legacy label) to a building_type key; unknown text -> 'other'."""
    if not value:
        return False
    text = str(value).strip().lower()
    for key, label in BUILDING_TYPES:
        if text in (key, label.lower()):
            return key
    return _BUILDING_TYPE_ALIASES.get(text, 'other')


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
    registration_fee_total = fields.Monetary(
        string="Registration Fees", compute='_compute_registration_dues', currency_field='currency_id',
        help="Fees the union's loan type charges at registration (or the branch membership fee).")
    registration_fee_residual = fields.Monetary(
        string="Registration Fees Due", compute='_compute_registration_dues', currency_field='currency_id')
    registration_savings_required = fields.Monetary(
        string="Savings Before Approval", compute='_compute_registration_dues', currency_field='currency_id')
    registration_savings_due = fields.Monetary(
        string="Savings Still Required", compute='_compute_registration_dues', currency_field='currency_id')

    # ---- KYC -------------------------------------------------------------
    nin = fields.Char(string="National ID Number (NIN)", copy=False)
    bvn = fields.Char(string="Bank Verification Number (BVN)", copy=False)
    id_card_image = fields.Image(
        string="ID Card", max_width=1600, max_height=1600, attachment=True, copy=False,
        help="Photo of the member's ID card taken by the credit officer at enrolment.")
    next_of_kin = fields.Char()
    next_of_kin_relationship = fields.Char()
    next_of_kin_phone = fields.Char()
    next_of_kin_email = fields.Char()

    # ---- residence / business survey -------------------------------------
    landmark = fields.Char()
    building_type = fields.Selection(BUILDING_TYPES)
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
    can_apply_loan = fields.Boolean(
        compute='_compute_can_apply_loan', string="Can Apply for a Loan",
        help="No other open loan blocks a new application (see the early application window in settings).")

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

    @api.depends('is_ranchi_member', 'membership_state', 'union_id.loan_type_id', 'company_id',
                 'membership_fee_move_id.amount_residual', 'membership_fee_move_id.state')
    def _compute_registration_dues(self):
        for partner in self:
            if not partner.is_ranchi_member or not partner.union_id:
                partner.update({'registration_fee_total': 0.0, 'registration_fee_residual': 0.0,
                                'registration_savings_required': 0.0, 'registration_savings_due': 0.0})
                continue
            partner.registration_fee_total = sum(item['amount'] for item in partner._registration_fee_items())
            partner.registration_fee_residual = partner._registration_fee_residual()
            required = partner.union_id.loan_type_id.registration_savings if partner._in_registration() else 0.0
            partner.registration_savings_required = required
            partner.registration_savings_due = max(0.0, required - partner.savings_balance)

    @api.depends('loan_ids.state', 'loan_ids.balance')
    def _compute_loan_stats(self):
        for partner in self:
            loans = partner.loan_ids
            partner.loan_count = len(loans)
            active = loans.filtered(lambda l: l.state in ('applied', 'approved', 'fees', 'disbursed'))
            # Prefer the running loan: an early application for the next loan must not hide it.
            partner.active_loan_id = active.filtered(lambda l: l.state == 'disbursed')[:1] or active[:1]
            partner.loan_cycle = len(loans.filtered(lambda l: l.state == 'paid'))
            partner.loans_outstanding = sum(active.mapped('balance'))

    @api.depends('loan_ids.state', 'loan_ids.installment_ids.state')
    def _compute_can_apply_loan(self):
        for partner in self:
            partner.can_apply_loan = not partner._loan_application_block()

    def _loan_application_block(self, exclude=None):
        """Return why *self* cannot open another loan application now, or False.

        With one active loan per member, the only open loan allowed beside a new
        application is a disbursed one with at most the company's early application
        window of unpaid installments left. That loan must be fully paid before
        the new one is disbursed (see ``ranchi.loan._check_previous_loan_paid``).
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        if not company.ranchi_one_active_loan:
            return False
        others = self.sudo().loan_ids.filtered(
            lambda l: l.state in ('applied', 'approved', 'fees', 'disbursed') and l.id not in (exclude or l.browse()).ids)
        if not others:
            return False
        window = company.ranchi_early_application_installments
        if window > 0 and len(others) == 1 and others.state == 'disbursed':
            left = len(others.installment_ids.filtered(lambda i: i.state != 'paid'))
            if left <= window:
                return False
            return _(
                "%(member)s still has %(left)s installment(s) to pay on loan %(loan)s; "
                "a new application is allowed from %(window)s or fewer.",
                member=self.display_name, left=left, loan=others.name, window=window)
        return _("%s already has an active loan.", self.display_name)

    def _early_application_loan(self, exclude=None):
        """The disbursed, not yet paid loan a new application of *self* runs beside, if any."""
        self.ensure_one()
        return self.sudo().loan_ids.filtered(
            lambda l: l.state == 'disbursed' and l.id not in (exclude or l.browse()).ids)[:1].sudo(False)

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

    # ------------------------------------------------------------------
    # Registration fees and savings, collected by the credit officer
    # ------------------------------------------------------------------
    def _in_registration(self):
        self.ensure_one()
        return self.membership_state in ('applied', 'vetted')

    def _registration_fee_items(self):
        """Fees due at registration: [{'name', 'amount', 'product'}]. The union's loan type lists
        them; without any there, the branch membership fee applies. 'product' is a record or the
        name of the branch setting holding it."""
        self.ensure_one()
        company = self._ranchi_company()
        currency = company.currency_id
        fees = self.union_id.loan_type_id.registration_fee_ids
        if fees:
            items = [{'name': fee.name, 'amount': fee._amount_for(0.0, currency),
                      'product': fee.product_id or 'ranchi_membership_product_id'} for fee in fees]
        else:
            items = [{'name': _("Membership Fee"), 'amount': company.ranchi_membership_fee or 0.0,
                      'product': 'ranchi_membership_product_id'}]
        return [item for item in items if not currency.is_zero(item['amount'])]

    def _registration_fee_residual(self):
        """Registration fees not paid yet: the open invoice's residual, or every fee while the
        member is in registration and nothing has been invoiced."""
        self.ensure_one()
        invoice = self.sudo().membership_fee_move_id
        if invoice and invoice.state == 'posted':
            return invoice.amount_residual
        if invoice and invoice.state == 'draft':
            return invoice.amount_total
        if not self._in_registration():
            return 0.0
        return sum(item['amount'] for item in self._registration_fee_items())

    def _create_registration_invoice(self, amount=None):
        """Invoice the registration fees (or *amount* as a single membership fee line) and post it."""
        self.ensure_one()
        company = self._ranchi_company()
        journal = company._ranchi_require('ranchi_fee_journal_id')
        if amount is None:
            items = self._registration_fee_items()
        else:
            items = [{'name': _("Membership Fee"), 'amount': amount, 'product': 'ranchi_membership_product_id'}]
        if not items:
            raise UserError(_("No registration fee is configured for %s.", self.display_name))
        lines = []
        for item in items:
            product = item['product']
            if isinstance(product, str):
                product = company._ranchi_require(product)
            lines.append((0, 0, {
                'product_id': product.id,
                'name': f"{item['name']} - {self.name}",
                'quantity': 1.0,
                'price_unit': item['amount'],
                'tax_ids': [(6, 0, product.taxes_id.filtered(lambda t: t.company_id == company).ids)],
            }))
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.id,
            'company_id': company.id,
            'journal_id': journal.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': _("Membership %s", self.name),
            'ranchi_move_type': 'membership_fee',
            'ranchi_member_id': self.id,
            'invoice_line_ids': lines,
        })
        invoice.action_post()
        self.membership_fee_move_id = invoice
        return invoice

    def _fee_invoices_to_settle(self):
        """Open fee invoices a field collection pays, oldest obligation first: registration fees,
        then the fees of approved loans. Invoices still missing are created (call as superuser)."""
        self.ensure_one()
        invoices = self.env['account.move']
        reg = self.membership_fee_move_id
        if reg and reg.state == 'draft':
            reg.action_post()
        if (not reg or reg.state == 'cancel') and self._in_registration() and self._registration_fee_items():
            reg = self._create_registration_invoice()
        if reg and reg.state == 'posted':
            invoices |= reg
        for loan in self.loan_ids.filtered(lambda l: l.state == 'approved').sorted('id'):
            inv = loan.fee_move_id
            if inv and inv.state == 'draft':
                inv.action_post()
            if (not inv or inv.state == 'cancel') and loan._fee_items():
                inv = loan._create_fee_invoice()
            if inv and inv.state == 'posted':
                invoices |= inv
        return invoices.filtered(lambda inv: not inv.currency_id.is_zero(inv.amount_residual))

    def _fees_due(self):
        """Every fee the credit officer can still collect from this member, oldest first:
        [{'kind': 'registration'|'loan', 'loan': loan or None, 'name', 'amount', 'due'}].
        'due' spreads what has been paid on an invoice over its items in order."""
        self.ensure_one()
        currency = self._ranchi_company().currency_id
        result = []

        def spread(kind, loan, items, residual):
            paid = sum(item['amount'] for item in items) - residual
            for item in items:
                covered = min(item['amount'], max(paid, 0.0))
                paid -= covered
                due = currency.round(item['amount'] - covered)
                result.append({'kind': kind, 'loan': loan, 'name': item['name'],
                               'amount': item['amount'], 'due': due})

        reg_residual = self._registration_fee_residual()
        if not currency.is_zero(reg_residual):
            invoice = self.sudo().membership_fee_move_id
            if invoice and invoice.state in ('draft', 'posted'):
                items = [{'name': line.name, 'amount': line.price_total}
                         for line in invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product')]
            else:
                items = self._registration_fee_items()
            spread('registration', None, items, reg_residual)
        for loan in self.loan_ids.filtered(lambda l: l.state == 'approved').sorted('id'):
            if currency.is_zero(loan.fee_residual):
                continue
            invoice = loan.sudo().fee_move_id
            if invoice and invoice.state in ('draft', 'posted'):
                items = [{'name': line.name, 'amount': line.price_total}
                         for line in invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product')]
            else:
                items = loan._fee_items()
            spread('loan', loan, items, loan.fee_residual)
        return [fee for fee in result if not currency.is_zero(fee['due'])]

    def _fees_due_total(self):
        self.ensure_one()
        return self._registration_fee_residual() + sum(
            self.loan_ids.filtered(lambda l: l.state == 'approved').mapped('fee_residual'))

    def _pending_collected(self, field_name):
        """Amount of *field_name* on this member's collection lines handed over but not posted yet."""
        self.ensure_one()
        lines = self.env['ranchi.collection.line'].sudo().search([
            ('member_id', '=', self.id), ('state', '=', 'submitted')])
        return sum(lines.mapped(field_name))

    def _field_dues(self):
        """What the credit officer still has to collect from this member, net of collections
        already handed over but not posted: {'fees': [...], 'fees_total', 'savings_required',
        'savings_due'}. Fees are the items of _fees_due() with their 'due' reduced in order."""
        self.ensure_one()
        currency = self._ranchi_company().currency_id
        pending = self._pending_collected('amount_fees')
        fees = []
        for fee in self._fees_due():
            taken = min(fee['due'], pending)
            pending -= taken
            due = currency.round(fee['due'] - taken)
            if not currency.is_zero(due):
                fees.append(dict(fee, due=due))
        savings_due = max(0.0, self.registration_savings_due - self._pending_collected('amount_savings'))
        return {
            'fees': fees,
            'fees_total': currency.round(sum(fee['due'] for fee in fees)),
            'savings_required': self.registration_savings_required,
            'savings_due': currency.round(savings_due),
        }

    def _check_registration_complete(self):
        """A member is only confirmed once the loan type's registration fees are paid and the
        savings before approval are in."""
        for partner in self:
            loan_type = partner.union_id.loan_type_id
            currency = partner._ranchi_company().currency_id
            if loan_type.registration_fee_ids and not currency.is_zero(partner._registration_fee_residual()):
                raise UserError(_(
                    "%(member)s still owes %(due)s of registration fees. The credit officer collects them "
                    "with the union's collection.", member=partner.display_name,
                    due=currency.format(partner._registration_fee_residual())))
            required = loan_type.registration_savings
            if required and currency.compare_amounts(partner.savings_balance, required) < 0:
                raise UserError(_(
                    "%(member)s has %(balance)s in savings; %(type)s requires %(required)s before the "
                    "membership is approved.", member=partner.display_name,
                    balance=currency.format(partner.savings_balance), type=loan_type.name,
                    required=currency.format(required)))

    def action_open_membership_fee_wizard(self):
        self.ensure_one()
        if not self.is_ranchi_member:
            raise UserError(_("Mark the contact as a Ranchi member first."))
        if self.membership_state != 'applied':
            raise UserError(_("The membership fee is collected while the member is in 'Applied'."))
        invoice = self.membership_fee_move_id
        if invoice and invoice.state != 'cancel':
            # already invoiced, e.g. by a field collection: register the payment on that invoice
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': invoice.id,
                'view_mode': 'form',
                'target': 'current',
            }
        company = self._ranchi_company()
        return {
            'name': _('Collect Membership Fee'),
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.membership.fee.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_amount': sum(item['amount'] for item in self._registration_fee_items()),
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
            partner._check_registration_complete()
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
