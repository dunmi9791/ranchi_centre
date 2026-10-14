# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RanchiMembershipFeeWizard(models.TransientModel):
    _name = 'ranchi.membership.fee.wizard'
    _description = 'Collect Membership Fee'

    partner_id = fields.Many2one('res.partner', string="Member", required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount = fields.Monetary(string="Membership Fee", required=True)
    has_type_fees = fields.Boolean(compute='_compute_has_type_fees')
    register_payment = fields.Boolean(
        string="Register cash payment now", default=True,
        help="Also record the payment in the selected journal and reconcile it with the invoice.")
    journal_id = fields.Many2one(
        'account.journal', string="Payment Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)

    @api.depends('partner_id')
    def _compute_has_type_fees(self):
        for wiz in self:
            wiz.has_type_fees = bool(wiz.partner_id.union_id.loan_type_id.registration_fee_ids)

    def action_confirm(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("The membership fee must be positive."))
        partner = self.partner_id
        # The loan type's registration fees are invoiced line by line; the amount only
        # replaces the single branch membership fee.
        invoice = partner._create_registration_invoice(None if self.has_type_fees else self.amount)
        if self.register_payment:
            if not self.journal_id:
                raise UserError(_("Select the journal that received the payment."))
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids).create({
                    'journal_id': self.journal_id.id,
                    'amount': invoice.amount_total,
                    'payment_date': fields.Date.context_today(self),
                })._create_payments()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }
