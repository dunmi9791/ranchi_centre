# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import UserError

FEE_MOVE_TYPES = ('membership_fee', 'loan_fee')


def _fee_payment_error(env):
    return UserError(env._(
        "Registration fees and fees before disbursement are paid only through a credit officer's "
        "field collection. Add them to the union's collection sheet; posting the collection "
        "settles the invoice."))


class AccountMove(models.Model):
    _inherit = 'account.move'

    ranchi_move_type = fields.Selection(
        [('membership_fee', 'Membership Fee'),
         ('loan_fee', 'Loan Fees'),
         ('disbursement', 'Loan Disbursement'),
         ('collection', 'Field Collection'),
         ('savings', 'Savings Transaction'),
         ('lapse', 'Savings Adjustment'),
         ('writeoff', 'Loan Write-off')],
        string="Ranchi Operation", copy=False, index=True)
    ranchi_loan_id = fields.Many2one(
        'ranchi.loan', string="Loan", copy=False, index=True, ondelete='set null')
    ranchi_member_id = fields.Many2one(
        'res.partner', string="Ranchi Member", copy=False, index=True, ondelete='set null',
        help="Member concerned by a membership fee invoice.")

    def _invoice_paid_hook(self):
        res = super()._invoice_paid_hook()
        for move in self:
            if move.ranchi_move_type == 'loan_fee' and move.ranchi_loan_id:
                move.ranchi_loan_id._on_fees_paid()
            elif move.ranchi_move_type == 'membership_fee' and move.ranchi_member_id:
                move.ranchi_member_id._on_membership_fee_paid()
        return res

    def _ranchi_is_fee_invoice(self):
        self.ensure_one()
        return self.move_type == 'out_invoice' and self.ranchi_move_type in FEE_MOVE_TYPES

    def action_force_register_payment(self):
        if any(move._ranchi_is_fee_invoice() for move in self):
            raise _fee_payment_error(self.env)
        return super().action_force_register_payment()


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ranchi_installment_id = fields.Many2one(
        'ranchi.loan.installment', string="Loan Installment", copy=False, index=True,
        ondelete='set null')

    def action_register_payment(self, ctx=None):
        if any(move._ranchi_is_fee_invoice() for move in self.move_id):
            raise _fee_payment_error(self.env)
        return super().action_register_payment(ctx=ctx)

    def _reconcile_plan(self, reconciliation_plan):
        # Fee invoices are settled only by a field collection (or reversed by a credit note):
        # this blocks Register Payment, outstanding credits and manual reconciliation alike.
        if not self.env.context.get('no_exchange_difference'):
            for batch in reconciliation_plan:
                moves = self._ranchi_plan_lines(batch).move_id
                fee_invoices = moves.filtered(lambda m: m._ranchi_is_fee_invoice())
                others = moves - fee_invoices
                if fee_invoices and others.filtered(
                        lambda m: m.ranchi_move_type != 'collection' and m.move_type != 'out_refund'):
                    raise _fee_payment_error(self.env)
        return super()._reconcile_plan(reconciliation_plan)

    def _ranchi_plan_lines(self, plan):
        if isinstance(plan, models.BaseModel):
            return plan
        lines = self.browse()
        for item in plan:
            lines |= self._ranchi_plan_lines(item)
        return lines
