# -*- coding: utf-8 -*-
{
    'name': 'Ranchi Centre',
    'summary': 'Members, unions, loans, savings, collections and disbursements for Ranchi Empowerment Centre',
    'description': """
Single module replacing ranchi_loanee_management, ranchi_savings_management and ranchi_apis.

* Branches (companies) -> Unions (credit officer, union day) -> Members (res.partner)
* Membership pipeline Applied -> Vetted -> Confirmed with membership fee invoice
* Loan types, loan stages (cycle ladder), loans with generated installment schedule
* Fees invoiced before disbursement; principal booked to a loans receivable account
* Disbursement queue (manual, Flutterwave, Monnify) with idempotent webhook log
* Savings sub-ledger on a liability account: deposits, withdrawals, interest, fees, holds
* Field collections posting loan repayments and savings deposits in one journal entry
* Withdrawal requests with one or two approval levels, lapse adjustments, write-offs
* JSON API v1 for the credit officer app, member portal, savings statement report
    """,
    'author': 'Ranchi Empowerment Centre',
    'website': 'https://ranchi.example.com',
    'category': 'Accounting/Microfinance',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'account', 'hr', 'portal', 'web'],
    'data': [
        'security/ranchi_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/product_data.xml',
        'data/ir_cron_data.xml',
        'views/dashboard_views.xml',
        'views/res_config_settings_views.xml',
        'views/ranchi_union_views.xml',
        'views/res_partner_views.xml',
        'views/loan_type_views.xml',
        'views/loan_installment_views.xml',
        'views/loan_repayment_views.xml',
        'views/loan_views.xml',
        'views/disbursement_views.xml',
        'views/collection_views.xml',
        'views/savings_transaction_views.xml',
        'views/savings_rate_views.xml',
        'views/withdrawal_request_views.xml',
        'views/lapse_adjustment_views.xml',
        'views/gateway_event_views.xml',
        'views/api_request_views.xml',
        'views/account_move_views.xml',
        'wizards/membership_fee_wizard_views.xml',
        'wizards/savings_deposit_wizard_views.xml',
        'wizards/withdrawal_pay_wizard_views.xml',
        'wizards/interest_generate_wizard_views.xml',
        'wizards/loan_writeoff_wizard_views.xml',
        'wizards/savings_statement_wizard_views.xml',
        'report/savings_statement_report.xml',
        'views/portal_templates.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ranchi_centre/static/src/dashboard/dashboard.scss',
            'ranchi_centre/static/src/dashboard/dashboard.js',
            'ranchi_centre/static/src/dashboard/dashboard.xml',
        ],
    },
    'demo': [
        'demo/ranchi_demo.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
}
