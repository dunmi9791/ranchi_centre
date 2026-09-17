# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ranchi_branch_code = fields.Char(related='company_id.ranchi_branch_code', readonly=False)

    ranchi_loan_receivable_account_id = fields.Many2one(
        related='company_id.ranchi_loan_receivable_account_id', readonly=False)
    ranchi_service_income_account_id = fields.Many2one(
        related='company_id.ranchi_service_income_account_id', readonly=False)
    ranchi_savings_liability_account_id = fields.Many2one(
        related='company_id.ranchi_savings_liability_account_id', readonly=False)
    ranchi_fee_income_account_id = fields.Many2one(
        related='company_id.ranchi_fee_income_account_id', readonly=False)
    ranchi_interest_expense_account_id = fields.Many2one(
        related='company_id.ranchi_interest_expense_account_id', readonly=False)
    ranchi_writeoff_account_id = fields.Many2one(
        related='company_id.ranchi_writeoff_account_id', readonly=False)

    ranchi_disbursement_journal_id = fields.Many2one(
        related='company_id.ranchi_disbursement_journal_id', readonly=False)
    ranchi_collection_journal_id = fields.Many2one(
        related='company_id.ranchi_collection_journal_id', readonly=False)
    ranchi_savings_journal_id = fields.Many2one(
        related='company_id.ranchi_savings_journal_id', readonly=False)
    ranchi_fee_journal_id = fields.Many2one(
        related='company_id.ranchi_fee_journal_id', readonly=False)

    ranchi_membership_fee = fields.Monetary(
        related='company_id.ranchi_membership_fee', readonly=False, currency_field='currency_id')
    ranchi_membership_product_id = fields.Many2one(
        related='company_id.ranchi_membership_product_id', readonly=False)
    ranchi_auto_vet_on_fee = fields.Boolean(related='company_id.ranchi_auto_vet_on_fee', readonly=False)

    ranchi_admin_fee_product_id = fields.Many2one(
        related='company_id.ranchi_admin_fee_product_id', readonly=False)
    ranchi_risk_premium_product_id = fields.Many2one(
        related='company_id.ranchi_risk_premium_product_id', readonly=False)
    ranchi_service_charge_product_id = fields.Many2one(
        related='company_id.ranchi_service_charge_product_id', readonly=False)
    ranchi_one_active_loan = fields.Boolean(related='company_id.ranchi_one_active_loan', readonly=False)

    ranchi_savings_min_balance = fields.Monetary(
        related='company_id.ranchi_savings_min_balance', readonly=False, currency_field='currency_id')
    ranchi_withdrawal_fee_percent = fields.Float(
        related='company_id.ranchi_withdrawal_fee_percent', readonly=False)
    ranchi_withdrawal_flat_fee = fields.Monetary(
        related='company_id.ranchi_withdrawal_flat_fee', readonly=False, currency_field='currency_id')
    ranchi_withdrawal_approval_levels = fields.Selection(
        related='company_id.ranchi_withdrawal_approval_levels', readonly=False)

    ranchi_disbursement_provider = fields.Selection(
        related='company_id.ranchi_disbursement_provider', readonly=False)
    ranchi_flutterwave_secret_key = fields.Char(
        related='company_id.ranchi_flutterwave_secret_key', readonly=False)
    ranchi_flutterwave_webhook_hash = fields.Char(
        related='company_id.ranchi_flutterwave_webhook_hash', readonly=False)
    ranchi_monnify_base_url = fields.Char(related='company_id.ranchi_monnify_base_url', readonly=False)
    ranchi_monnify_api_key = fields.Char(related='company_id.ranchi_monnify_api_key', readonly=False)
    ranchi_monnify_secret_key = fields.Char(related='company_id.ranchi_monnify_secret_key', readonly=False)
    ranchi_monnify_source_account = fields.Char(
        related='company_id.ranchi_monnify_source_account', readonly=False)
