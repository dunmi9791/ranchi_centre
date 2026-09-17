# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = 'res.company'

    ranchi_branch_code = fields.Char(
        string="Branch Code", size=8,
        help="Short code used as the prefix of member numbers, e.g. ABJ.")

    # ---- accounts --------------------------------------------------------
    ranchi_loan_receivable_account_id = fields.Many2one(
        'account.account', string="Loans Receivable Account",
        check_company=True,
        domain="[('deprecated', '=', False), ('reconcile', '=', True)]",
        help="Asset account holding principal and service charge owed by members. Must allow reconciliation.")
    ranchi_service_income_account_id = fields.Many2one(
        'account.account', string="Service Charge Income Account", check_company=True,
        domain="[('deprecated', '=', False)]")
    ranchi_savings_liability_account_id = fields.Many2one(
        'account.account', string="Member Savings Liability Account", check_company=True,
        domain="[('deprecated', '=', False)]",
        help="Liability account whose partner balances are the members' savings.")
    ranchi_fee_income_account_id = fields.Many2one(
        'account.account', string="Withdrawal Fee Income Account", check_company=True,
        domain="[('deprecated', '=', False)]")
    ranchi_interest_expense_account_id = fields.Many2one(
        'account.account', string="Savings Interest Expense Account", check_company=True,
        domain="[('deprecated', '=', False)]")
    ranchi_writeoff_account_id = fields.Many2one(
        'account.account', string="Loan Write-off Account", check_company=True,
        domain="[('deprecated', '=', False)]")

    # ---- journals --------------------------------------------------------
    ranchi_disbursement_journal_id = fields.Many2one(
        'account.journal', string="Disbursement Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    ranchi_collection_journal_id = fields.Many2one(
        'account.journal', string="Default Collection Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    ranchi_savings_journal_id = fields.Many2one(
        'account.journal', string="Savings Adjustments Journal", check_company=True,
        domain="[('type', '=', 'general')]",
        help="Miscellaneous journal used for interest accruals, withdrawal fees and write-offs.")
    ranchi_fee_journal_id = fields.Many2one(
        'account.journal', string="Fee Invoices Journal", check_company=True,
        domain="[('type', '=', 'sale')]",
        help="Sales journal for membership and loan fee invoices.")

    # ---- membership ------------------------------------------------------
    ranchi_membership_fee = fields.Monetary(
        string="Membership Fee", currency_field='currency_id', default=0.0)
    ranchi_membership_product_id = fields.Many2one(
        'product.product', string="Membership Fee Product", check_company=True)
    ranchi_auto_vet_on_fee = fields.Boolean(
        string="Vet member when membership fee is paid", default=True)

    # ---- loans -----------------------------------------------------------
    ranchi_admin_fee_product_id = fields.Many2one(
        'product.product', string="Administration Fee Product", check_company=True)
    ranchi_risk_premium_product_id = fields.Many2one(
        'product.product', string="Risk Premium Product", check_company=True)
    ranchi_service_charge_product_id = fields.Many2one(
        'product.product', string="Service Charge Product", check_company=True)
    ranchi_one_active_loan = fields.Boolean(
        string="One active loan per member", default=True)

    # ---- savings ---------------------------------------------------------
    ranchi_savings_min_balance = fields.Monetary(
        string="Minimum Savings Balance", currency_field='currency_id', default=0.0)
    ranchi_withdrawal_fee_percent = fields.Float(string="Withdrawal Fee (%)", default=0.0)
    ranchi_withdrawal_flat_fee = fields.Monetary(
        string="Withdrawal Flat Fee", currency_field='currency_id', default=0.0)
    ranchi_withdrawal_approval_levels = fields.Selection(
        [('1', 'One level'), ('2', 'Two levels')],
        string="Withdrawal Approval Levels", default='1', required=True)

    # ---- disbursement gateway ---------------------------------------------
    ranchi_disbursement_provider = fields.Selection(
        [('manual', 'Manual bank transfer'),
         ('flutterwave', 'Flutterwave Transfers'),
         ('monnify', 'Monnify Disbursements')],
        string="Disbursement Provider", default='manual', required=True)
    ranchi_flutterwave_secret_key = fields.Char(string="Flutterwave Secret Key", groups='base.group_system')
    ranchi_flutterwave_webhook_hash = fields.Char(
        string="Flutterwave Webhook Hash", groups='base.group_system',
        help="The 'verif-hash' value configured in the Flutterwave dashboard.")
    ranchi_monnify_base_url = fields.Char(
        string="Monnify Base URL", default='https://api.monnify.com', groups='base.group_system')
    ranchi_monnify_api_key = fields.Char(string="Monnify API Key", groups='base.group_system')
    ranchi_monnify_secret_key = fields.Char(string="Monnify Secret Key", groups='base.group_system')
    ranchi_monnify_source_account = fields.Char(
        string="Monnify Source Account", groups='base.group_system',
        help="Wallet account number the disbursements are debited from.")

    def _ranchi_require(self, field_name):
        """Return the configured record for *field_name* or raise a clear error."""
        self.ensure_one()
        value = self[field_name]
        if not value:
            label = self._fields[field_name].string
            raise UserError(_(
                "%(label)s is not configured for %(company)s. "
                "Set it under Ranchi Centre > Configuration > Settings.",
                label=label, company=self.display_name))
        return value

    @api.constrains('ranchi_loan_receivable_account_id')
    def _check_loan_receivable_reconcilable(self):
        for company in self:
            account = company.ranchi_loan_receivable_account_id
            if account and not account.reconcile:
                raise UserError(_("The Loans Receivable account must allow reconciliation."))
