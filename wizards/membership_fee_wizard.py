# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class RanchiMembershipFeeWizard(models.TransientModel):
    _name = 'ranchi.membership.fee.wizard'
    _description = 'Collect Membership Fee'

    partner_id = fields.Many2one('res.partner', string="Member", required=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    amount = fields.Monetary(string="Membership Fee", required=True)
    register_payment = fields.Boolean(
        string="Register cash payment now", default=True,
        help="Also record the payment in the selected journal and reconcile it with the invoice.")
    journal_id = fields.Many2one(
        'account.journal', string="Payment Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env.company.ranchi_collection_journal_id)

    def action_confirm(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("The membership fee must be positive."))
        company = self.company_id
        product = company._ranchi_require('ranchi_membership_product_id')
        journal = company._ranchi_require('ranchi_fee_journal_id')
        partner = self.partner_id
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'company_id': company.id,
            'journal_id': journal.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': _("Membership %s", partner.name),
            'ranchi_move_type': 'membership_fee',
            'ranchi_member_id': partner.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id,
                'name': _("Membership Fee - %s", partner.name),
                'quantity': 1.0,
                'price_unit': self.amount,
                'tax_ids': [(6, 0, product.taxes_id.filtered(lambda t: t.company_id == company).ids)],
            })],
        })
        invoice.action_post()
        partner.membership_fee_move_id = invoice
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
