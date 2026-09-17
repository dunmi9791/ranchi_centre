# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class RanchiMemberPortal(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id
        if 'ranchi_savings_count' in counters:
            values['ranchi_savings_count'] = request.env['ranchi.savings.transaction'].search_count(
                [('member_id', '=', partner.id), ('state', '=', 'posted'), ('type', '!=', 'hold')]) \
                if partner.is_ranchi_member else 0
        if 'ranchi_loan_count' in counters:
            values['ranchi_loan_count'] = request.env['ranchi.loan'].search_count(
                [('member_id', '=', partner.id)]) if partner.is_ranchi_member else 0
        return values

    @http.route(['/my/savings', '/my/savings/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_savings(self, page=1, **kw):
        partner = request.env.user.partner_id
        Tx = request.env['ranchi.savings.transaction']
        domain = [('member_id', '=', partner.id), ('state', '=', 'posted'), ('type', '!=', 'hold')]
        total = Tx.search_count(domain)
        pager = portal_pager(url='/my/savings', total=total, page=page, step=self._items_per_page)
        transactions = Tx.search(domain, limit=self._items_per_page, offset=pager['offset'])
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'ranchi_savings',
            'partner': partner,
            'transactions': transactions,
            'pending_requests': request.env['ranchi.withdrawal.request'].search(
                [('member_id', '=', partner.id), ('state', 'in', ('submitted', 'approved_l1', 'approved'))]),
            'pager': pager,
        })
        return request.render('ranchi_centre.portal_my_savings', values)

    @http.route(['/my/loans'], type='http', auth='user', website=True)
    def portal_my_loans(self, **kw):
        partner = request.env.user.partner_id
        loans = request.env['ranchi.loan'].search(
            [('member_id', '=', partner.id), ('state', 'not in', ('draft', 'cancelled'))])
        values = self._prepare_portal_layout_values()
        values.update({'page_name': 'ranchi_loans', 'partner': partner, 'loans': loans})
        return request.render('ranchi_centre.portal_my_loans', values)

    @http.route(['/my/loans/<int:loan_id>'], type='http', auth='user', website=True)
    def portal_my_loan(self, loan_id, **kw):
        loan = request.env['ranchi.loan'].browse(loan_id)
        if not loan.exists() or loan.member_id != request.env.user.partner_id:
            return request.redirect('/my/loans')
        values = self._prepare_portal_layout_values()
        values.update({'page_name': 'ranchi_loans', 'loan': loan})
        return request.render('ranchi_centre.portal_my_loan', values)
