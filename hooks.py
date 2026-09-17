# -*- coding: utf-8 -*-


def post_init_hook(env):
    """Point every company at the fee products shipped with the module.

    Company columns are created before data files load, so field defaults
    cannot reference the product xml-ids; we fill them here instead.
    """
    refs = {
        'ranchi_membership_product_id': 'ranchi_centre.product_membership_fee',
        'ranchi_admin_fee_product_id': 'ranchi_centre.product_loan_admin_fee',
        'ranchi_risk_premium_product_id': 'ranchi_centre.product_risk_premium',
        'ranchi_service_charge_product_id': 'ranchi_centre.product_service_charge',
    }
    vals = {}
    for field, xmlid in refs.items():
        product = env.ref(xmlid, raise_if_not_found=False)
        if product:
            vals[field] = product.id
    if vals:
        companies = env['res.company'].search([])
        for company in companies:
            company.write({k: v for k, v in vals.items() if not company[k]})
