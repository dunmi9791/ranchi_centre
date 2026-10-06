# -*- coding: utf-8 -*-
"""JSON API v1 for the credit-officer app.

Every route is JSON-RPC 2.0 over POST (Odoo ``type='json'``). Authentication is
an Odoo API key sent as ``Authorization: Bearer <key>``; the request then runs
as that user, so record rules (credit officer sees only their unions) apply.
Keys are created by the user under Preferences > Account Security.

Write endpoints accept an optional ``idempotencyKey``: replaying the same key
returns the first response instead of creating a second record.
"""
import json
import logging
from datetime import timedelta

from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from odoo import fields, http, _
from odoo.addons.base.models.res_users import INDEX_SIZE
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)
API_KEY_SCOPE = 'rpc'


def api_route(path, **kw):
    kw.setdefault('type', 'json')
    kw.setdefault('auth', 'public')
    kw.setdefault('methods', ['POST'])
    kw.setdefault('csrf', False)
    kw.setdefault('cors', '*')
    return http.route(path, **kw)


class RanchiApiV1(http.Controller):

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _authenticate(self):
        header = request.httprequest.headers.get('Authorization', '')
        if not header.lower().startswith('bearer '):
            raise Forbidden("Missing bearer API key")
        key = header[7:].strip()
        uid = request.env['res.users.apikeys'].sudo()._check_credentials(scope=API_KEY_SCOPE, key=key)
        if not uid:
            raise Forbidden("Invalid API key")
        request.update_env(user=uid)
        user = request.env.user
        if not user.has_group('ranchi_centre.group_ranchi_officer'):
            raise Forbidden("User is not a Ranchi credit officer")
        return user

    def _params(self, kw):
        data = dict(kw or {})
        if 'params' in data and isinstance(data['params'], dict) and len(data) == 1:
            data = data['params']
        return data

    def _idempotent(self, data, route, producer):
        key = data.get('idempotencyKey')
        if not key:
            return producer()
        Log = request.env['ranchi.api.request'].sudo()
        existing = Log.search([('key', '=', str(key)), ('user_id', '=', request.env.uid)], limit=1)
        if existing:
            return json.loads(existing.response)
        result = producer()
        Log.create({'key': str(key), 'user_id': request.env.uid, 'route': route,
                    'response': json.dumps(result, default=str)})
        return result

    def _run(self, func):
        try:
            return func()
        except (UserError, ValidationError) as exc:
            raise BadRequest(str(exc))
        except AccessError as exc:
            raise Forbidden(str(exc))

    @staticmethod
    def _date(value):
        return value.isoformat() if value else None

    @staticmethod
    def _money(value):
        return round(value or 0.0, 2)

    def _paginate(self, data):
        try:
            limit = min(int(data.get('limit') or 100), 500)
            offset = max(int(data.get('offset') or 0), 0)
        except (TypeError, ValueError):
            raise BadRequest("limit and offset must be integers")
        return limit, offset

    def _require(self, data, *names):
        missing = [n for n in names if data.get(n) in (None, '', [])]
        if missing:
            raise BadRequest(f"Missing required field(s): {', '.join(missing)}")

    def _record(self, model, rec_id):
        try:
            rec = request.env[model].browse(int(rec_id))
        except (TypeError, ValueError):
            raise BadRequest("Invalid id")
        if not rec.exists():
            raise NotFound()
        try:
            rec.check_access('read')
        except AccessError:
            raise NotFound()
        return rec

    def _member(self, member_id):
        """A Ranchi member the user may work with: partners have no union record rule, so the
        member must belong to a union the user can read."""
        member = self._record('res.partner', member_id)
        if not member.is_ranchi_member or not member.union_id \
                or not request.env['ranchi.union'].search_count([('id', '=', member.union_id.id)]):
            raise NotFound()
        return member

    # ------------------------------------------------------------------
    # serializers
    # ------------------------------------------------------------------
    def _ser_union(self, u):
        return {
            'id': u.id, 'code': u.code, 'name': u.name, 'status': u.state,
            'unionDay': u.union_day,
            'meetingFrequency': u.meeting_frequency,
            'loanTypeId': u.loan_type_id.id,
            'loanTypeName': u.loan_type_id.name,
            'creditOfficerId': u.credit_officer_id.id or None,
            'creditOfficerName': u.credit_officer_id.name or None,
            'memberCount': u.member_count, 'confirmedMemberCount': u.confirmed_member_count,
            'activeLoanCount': u.active_loan_count,
            'loansOutstanding': self._money(u.loans_outstanding),
            'savingsTotal': self._money(u.savings_total),
            'branchId': u.company_id.id,
        }

    def _ser_member(self, p):
        return {
            'id': p.id, 'memberNumber': p.member_number, 'name': p.name,
            'contactNumber': p.phone or p.mobile, 'email': p.email,
            'status': p.membership_state, 'joinDate': self._date(p.membership_date),
            'unionId': p.union_id.id or None, 'unionName': p.union_id.name or None,
            'savingsBalance': self._money(p.savings_balance),
            'savingsAvailable': self._money(p.savings_available),
            'loansOutstanding': self._money(p.loans_outstanding),
            'activeLoanId': p.active_loan_id.id or None,
            'loanCycle': p.loan_cycle,
        }

    def _ser_loan_type(self, lt):
        return {
            'id': lt.id, 'name': lt.name, 'code': lt.code,
            'serviceRate': lt.service_rate, 'serviceCollection': lt.service_collection,
            'adminCharge': self._money(lt.admin_charge), 'riskPremiumRate': lt.risk_premium_rate,
            'installmentCount': lt.installment_count, 'installmentPeriod': lt.installment_period,
            'meetingFrequency': lt.meeting_frequency,
            'graceDays': lt.grace_days,
            'minAmount': self._money(lt.min_amount), 'maxAmount': self._money(lt.max_amount),
            'stages': [{'id': s.id, 'name': s.name, 'maxPrincipal': self._money(s.max_principal),
                        'minCycle': s.min_cycle} for s in lt.stage_ids],
        }

    def _ser_loan(self, l):
        return {
            'id': l.id, 'loanNumber': l.name, 'status': l.state,
            'memberId': l.member_id.id, 'memberName': l.member_id.name,
            'unionId': l.union_id.id or None, 'loanTypeId': l.loan_type_id.id,
            'stageId': l.stage_id.id or None,
            'amountApplied': self._money(l.amount_applied), 'amountApproved': self._money(l.amount_approved),
            'serviceAmount': self._money(l.service_amount), 'feeTotal': self._money(l.fee_total),
            'totalRepayable': self._money(l.total_repayable), 'installmentAmount': self._money(l.installment_amount),
            'amountPaid': self._money(l.amount_paid), 'balance': self._money(l.balance),
            'amountOverdue': self._money(l.amount_overdue),
            'applicationDate': self._date(l.date_application), 'disbursedDate': self._date(l.date_disbursed),
            'firstDueDate': self._date(l.date_first_due), 'nextDueDate': self._date(l.next_due_date),
            'totalInstallments': len(l.installment_ids), 'paidInstallments': l.installments_paid_count,
            'overdueInstallments': l.overdue_count,
        }

    def _ser_installment(self, i):
        return {
            'id': i.id, 'loanId': i.loan_id.id, 'loanNumber': i.loan_id.name, 'sequence': i.sequence,
            'memberId': i.member_id.id, 'memberName': i.member_id.name,
            'memberPhone': i.member_id.phone or i.member_id.mobile,
            'unionId': i.union_id.id or None, 'unionName': i.union_id.name or None,
            'dueDate': self._date(i.date_due), 'amount': self._money(i.amount_total),
            'amountPaid': self._money(i.amount_paid), 'amountResidual': self._money(i.amount_residual),
            'state': i.state, 'status': i.status, 'daysOverdue': i.days_overdue,
            'lastPaymentDate': self._date(i.last_payment_date),
        }

    def _ser_savings_tx(self, t):
        return {
            'id': t.id, 'reference': t.name, 'memberId': t.member_id.id, 'date': self._date(t.date),
            'type': t.type, 'amount': self._money(t.amount), 'signedAmount': self._money(t.signed_amount),
            'state': t.state, 'origin': t.origin, 'note': t.note,
        }

    def _ser_withdrawal(self, w):
        return {
            'id': w.id, 'reference': w.name, 'memberId': w.member_id.id, 'memberName': w.member_id.name,
            'amount': self._money(w.amount), 'fee': self._money(w.fee_amount), 'netAmount': self._money(w.net_amount),
            'requestDate': self._date(w.request_date), 'status': w.state, 'reason': w.reason or '',
        }

    def _ser_collection(self, c):
        return {
            'id': c.id, 'reference': c.name, 'date': self._date(c.date), 'unionId': c.union_id.id,
            'status': c.state, 'journalId': c.journal_id.id,
            'loanTotal': self._money(c.amount_loan_total), 'savingsTotal': self._money(c.amount_savings_total),
            'total': self._money(c.amount_total),
            'submittedAt': c.submitted_date and c.submitted_date.isoformat() or None,
            'cashReceived': self._money(c.amount_received) if c.received_by_id else None,
            'receivedBy': c.received_by_id.name or None,
            'returnReason': c.return_reason or None,
            'lines': [{'id': ln.id, 'memberId': ln.member_id.id, 'loanId': ln.loan_id.id or None,
                       'loanAmount': self._money(ln.amount_loan), 'savingsAmount': self._money(ln.amount_savings)}
                      for ln in c.line_ids],
        }

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------
    def _me_payload(self, user):
        employee = request.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
        unions = request.env['ranchi.union'].search([('state', '=', 'active')])
        return {
            'userId': user.id, 'name': user.name, 'email': user.email,
            'employeeId': employee.id or None,
            'branchId': user.company_id.id, 'branchName': user.company_id.name,
            'currency': user.company_id.currency_id.name,
            'isManager': user.has_group('ranchi_centre.group_ranchi_manager'),
            'isGeneralManager': user.has_group('ranchi_centre.group_ranchi_general_manager'),
            'managedLoanTypeIds': request.env['ranchi.loan.type'].search(
                [('manager_ids', 'in', user.id)]).ids,
            'withdrawalFeePercent': user.company_id.ranchi_withdrawal_fee_percent or 0.0,
            'unions': [self._ser_union(u) for u in unions],
        }

    @api_route('/api/v1/me')
    def me(self, **kw):
        return self._me_payload(self._authenticate())

    # ------------------------------------------------------------------
    # auth: device sign-in for the mobile app
    # ------------------------------------------------------------------
    @api_route('/api/v1/auth/login')
    def auth_login(self, **kw):
        """Exchange Odoo credentials for a persistent device API key.

        Body: {login, password, deviceName?}. Returns the ``/me`` payload plus
        ``apiKey``. The key shows up under Preferences > Account Security as
        ``deviceName`` and can be revoked there or through ``/auth/logout``.
        """
        data = self._params(kw)
        self._require(data, 'login', 'password')
        credential = {'type': 'password', 'login': data['login'], 'password': data['password']}
        try:
            auth_info = request.env['res.users'].authenticate(
                request.db, credential, {'interactive': False})
        except AccessDenied:
            raise Forbidden("Invalid login or password")
        request.update_env(user=auth_info['uid'])
        user = request.env.user
        if not user.has_group('ranchi_centre.group_ranchi_officer'):
            raise Forbidden("User is not a Ranchi credit officer")
        name = (data.get('deviceName') or 'Ranchi Officer app')[:100]
        # sudo() keeps the current user but allows a persistent (no expiry) key
        key = request.env['res.users.apikeys'].sudo()._generate(API_KEY_SCOPE, name, None)
        payload = self._me_payload(user)
        payload['apiKey'] = key
        payload['deviceName'] = name
        return payload

    @api_route('/api/v1/auth/logout')
    def auth_logout(self, **kw):
        """Revoke the API key used for this request (sign out of this device)."""
        user = self._authenticate()
        key = request.httprequest.headers.get('Authorization', '')[7:].strip()
        keys = request.env['res.users.apikeys'].sudo().search([
            ('user_id', '=', user.id), ('scope', '=', API_KEY_SCOPE), ('index', '=', key[:INDEX_SIZE])])
        keys._remove()
        return {'ok': True, 'removed': len(keys)}

    # ------------------------------------------------------------------
    # unions
    # ------------------------------------------------------------------
    @api_route('/api/v1/unions')
    def unions(self, **kw):
        self._authenticate()
        data = self._params(kw)
        limit, offset = self._paginate(data)
        domain = [] if data.get('includeInactive') else [('state', '=', 'active')]
        unions = request.env['ranchi.union'].search(domain, limit=limit, offset=offset, order='name')
        return [self._ser_union(u) for u in unions]

    @api_route('/api/v1/unions/<int:union_id>')
    def union(self, union_id, **kw):
        self._authenticate()
        return self._ser_union(self._record('ranchi.union', union_id))

    @api_route('/api/v1/unions/<int:union_id>/members')
    def union_members(self, union_id, **kw):
        self._authenticate()
        union = self._record('ranchi.union', union_id)
        return [self._ser_member(m) for m in union.member_ids.sorted('name')]

    @api_route('/api/v1/unions/<int:union_id>/installments')
    def union_installments(self, union_id, **kw):
        self._authenticate()
        union = self._record('ranchi.union', union_id)
        data = self._params(kw)
        domain = [('union_id', '=', union.id), ('loan_state', '=', 'disbursed')]
        status = data.get('status')
        if status:
            domain.append(('status', 'in', status if isinstance(status, list) else [status]))
        insts = request.env['ranchi.loan.installment'].search(domain, order='date_due, id')
        return [self._ser_installment(i) for i in insts]

    # ------------------------------------------------------------------
    # members
    # ------------------------------------------------------------------
    @api_route('/api/v1/members')
    def members(self, **kw):
        self._authenticate()
        data = self._params(kw)
        limit, offset = self._paginate(data)
        domain = [('is_ranchi_member', '=', True)]
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        if data.get('status'):
            domain.append(('membership_state', '=', data['status']))
        if data.get('search'):
            domain += ['|', '|', ('name', 'ilike', data['search']), ('member_number', 'ilike', data['search']),
                       ('phone', 'ilike', data['search'])]
        # members are visible through their union: filter to unions the user can read
        unions = request.env['ranchi.union'].search([])
        domain.append(('union_id', 'in', unions.ids))
        members = request.env['res.partner'].search(domain, limit=limit, offset=offset, order='name')
        return [self._ser_member(m) for m in members]

    @api_route('/api/v1/members/<int:member_id>')
    def member(self, member_id, **kw):
        self._authenticate()
        return self._ser_member(self._member(member_id))

    @api_route('/api/v1/members/create')
    def member_create(self, **kw):
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'name', 'unionId')

        def create():
            union = self._record('ranchi.union', data['unionId'])
            vals = {
                'name': data['name'],
                'phone': data.get('contactNumber') or data.get('phone'),
                'email': data.get('email'),
                'union_id': union.id,
                'company_id': union.company_id.id,
                'is_ranchi_member': True,
                'membership_state': 'applied',
                'nin': data.get('nin'),
                'bvn': data.get('bvn'),
                'street': data.get('address'),
                'landmark': data.get('landmark'),
                'building_type': data.get('buildingType'),
                'years_of_occupancy': data.get('yearsOfOccupancy'),
                'number_of_portraits': data.get('numberOfPortraits'),
                'occupancy_details': data.get('occupancyDetails'),
                'type_items': data.get('typeOfItems'),
                'business_description': data.get('businessDescription'),
                'next_of_kin': data.get('nextOfKin'),
                'next_of_kin_relationship': data.get('nextOfKinRelationship'),
                'next_of_kin_phone': data.get('nextOfKinPhone'),
                'next_of_kin_email': data.get('nextOfKinEmail'),
            }
            vals = {k: v for k, v in vals.items() if v not in (None, '')}
            # Internal users can only read contacts; the officer's right to enrol into this union
            # was checked by _record(), so the partner itself is created as superuser.
            partner = request.env['res.partner'].sudo().with_company(union.company_id).create(vals)
            return self._ser_member(partner.with_env(request.env))

        return self._run(lambda: self._idempotent(data, 'members/create', create))

    @api_route('/api/v1/members/<int:member_id>/loans')
    def member_loans(self, member_id, **kw):
        self._authenticate()
        member = self._member(member_id)
        loans = request.env['ranchi.loan'].search([('member_id', '=', member.id)])
        return [self._ser_loan(l) for l in loans]

    @api_route('/api/v1/members/<int:member_id>/savings')
    def member_savings(self, member_id, **kw):
        self._authenticate()
        member = self._member(member_id)
        data = self._params(kw)
        limit, offset = self._paginate(data)
        txs = request.env['ranchi.savings.transaction'].search(
            [('member_id', '=', member.id), ('state', '=', 'posted'), ('type', '!=', 'hold')],
            limit=limit, offset=offset)
        return {
            'memberId': member.id,
            'balance': self._money(member.savings_balance),
            'onHold': self._money(member.savings_on_hold),
            'available': self._money(member.savings_available),
            'transactions': [self._ser_savings_tx(t) for t in txs],
        }

    # ------------------------------------------------------------------
    # loan types & loans
    # ------------------------------------------------------------------
    @api_route('/api/v1/loantypes')
    def loan_types(self, **kw):
        self._authenticate()
        types = request.env['ranchi.loan.type'].search([])
        return [self._ser_loan_type(lt) for lt in types]

    @api_route('/api/v1/loans')
    def loans(self, **kw):
        self._authenticate()
        data = self._params(kw)
        limit, offset = self._paginate(data)
        domain = []
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        if data.get('status'):
            status = data['status']
            domain.append(('state', 'in', status if isinstance(status, list) else [status]))
        loans = request.env['ranchi.loan'].search(domain, limit=limit, offset=offset)
        return [self._ser_loan(l) for l in loans]

    @api_route('/api/v1/loans/<int:loan_id>')
    def loan(self, loan_id, **kw):
        self._authenticate()
        loan = self._record('ranchi.loan', loan_id)
        result = self._ser_loan(loan)
        result['installments'] = [self._ser_installment(i) for i in loan.installment_ids]
        return result

    @api_route('/api/v1/loans/create')
    def loan_create(self, **kw):
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'memberId', 'loanTypeId', 'amount')

        def create():
            member = self._member(data['memberId'])
            loan_type = self._record('ranchi.loan.type', data['loanTypeId'])
            vals = {
                'member_id': member.id,
                'company_id': member.company_id.id or request.env.company.id,
                'loan_type_id': loan_type.id,
                'stage_id': int(data['stageId']) if data.get('stageId') else False,
                'amount_applied': float(data['amount']),
                'date_first_due': data.get('firstDueDate') or data.get('dateFirst') or False,
                'purpose': data.get('purpose'),
                'avg_monthly_income': data.get('averageMonthlyIncome') or 0.0,
                'has_family_in_union': bool(data.get('familyMemberRegisteredInUnion')),
                'family_member_name': data.get('familyMemberName'),
                'is_indebted_elsewhere': bool(data.get('indebtedElsewhere')),
                'indebted_amount': data.get('indebtedAmount') or 0.0,
                'indebted_institution': data.get('indebtedInstitution'),
                'last_loan_amount': data.get('lastLoanAmount') or 0.0,
                'last_loan_paid_date': data.get('lastLoanPaidDate') or False,
                'guarantor_name': data.get('guarantorName'),
                'guarantor_relationship': data.get('guarantorRelationship'),
                'guarantor_phone': data.get('guarantorPhone'),
                'guarantor_home_address': data.get('guarantorHomeAddress'),
                'guarantor_office_address': data.get('guarantorOfficeAddress'),
            }
            vals = {k: v for k, v in vals.items() if v not in (None, '')}
            loan = request.env['ranchi.loan'].create(vals)
            if data.get('submit', True):
                loan.action_apply()
            return self._ser_loan(loan)

        return self._run(lambda: self._idempotent(data, 'loans/create', create))

    # ------------------------------------------------------------------
    # installments
    # ------------------------------------------------------------------
    def _installments(self, domain):
        insts = request.env['ranchi.loan.installment'].search(
            domain + [('loan_state', '=', 'disbursed')], order='date_due, union_id, id')
        return [self._ser_installment(i) for i in insts]

    @api_route('/api/v1/installments/today')
    def installments_today(self, **kw):
        self._authenticate()
        data = self._params(kw)
        day = fields.Date.from_string(data['date']) if data.get('date') else fields.Date.context_today(request.env.user)
        domain = [('date_due', '=', day)]
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        return self._installments(domain)

    @api_route('/api/v1/installments/overdue')
    def installments_overdue(self, **kw):
        self._authenticate()
        data = self._params(kw)
        domain = [('status', '=', 'overdue')]
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        return self._installments(domain)

    @api_route('/api/v1/installments/upcoming')
    def installments_upcoming(self, **kw):
        self._authenticate()
        data = self._params(kw)
        today = fields.Date.context_today(request.env.user)
        try:
            days = int(data.get('days') or 7)
        except (TypeError, ValueError):
            raise BadRequest("days must be an integer")
        domain = [('state', '!=', 'paid'), ('date_due', '>=', today),
                  ('date_due', '<=', today + timedelta(days=days))]
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        return self._installments(domain)

    # ------------------------------------------------------------------
    # collections (the app's "pay installment" flow)
    # ------------------------------------------------------------------
    @api_route('/api/v1/payment_journals')
    def payment_journals(self, **kw):
        self._authenticate()
        # Credit officers have no accounting group: list the branch's cash/bank journals as superuser.
        company = request.env.company
        journals = request.env['account.journal'].sudo().search([
            ('type', 'in', ('bank', 'cash')), ('company_id', '=', company.id)])
        default_id = company.sudo().ranchi_collection_journal_id.id
        return [{'id': j.id, 'name': j.name, 'code': j.code, 'type': j.type,
                 'isDefault': j.id == default_id} for j in journals]

    @api_route('/api/v1/collections/create')
    def collection_create(self, **kw):
        """Create a collection and submit it for cash handover.

        A manager confirms the cash received and posts it from the backend.
        Body: {unionId, journalId?, date?, lines: [{memberId, loanId?, loanAmount?, savingsAmount?}],
               submit? (default true; false keeps a draft), idempotencyKey?}
        """
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'unionId', 'lines')

        def create():
            union = self._record('ranchi.union', data['unionId'])
            journal_id = int(data['journalId']) if data.get('journalId') else \
                union.company_id.ranchi_collection_journal_id.id
            if not journal_id:
                raise BadRequest("No journal given and no default collection journal configured")
            lines = []
            for ln in data['lines']:
                if not ln.get('memberId'):
                    raise BadRequest("Each line needs a memberId")
                lines.append((0, 0, {
                    'member_id': int(ln['memberId']),
                    'loan_id': int(ln['loanId']) if ln.get('loanId') else False,
                    'amount_loan': float(ln.get('loanAmount') or 0.0),
                    'amount_savings': float(ln.get('savingsAmount') or 0.0),
                    'note': ln.get('note'),
                }))
            collection = request.env['ranchi.collection'].create({
                'union_id': union.id,
                'company_id': union.company_id.id,
                'journal_id': journal_id,
                'date': data.get('date') or fields.Date.context_today(request.env.user),
                'note': data.get('note'),
                'line_ids': lines,
            })
            # 'post' is the field's old name; officers can no longer post, only hand over.
            if data.get('submit', data.get('post', True)):
                collection.action_submit()
            return self._ser_collection(collection)

        return self._run(lambda: self._idempotent(data, 'collections/create', create))

    @api_route('/api/v1/installments/pay')
    def installment_pay(self, **kw):
        """Backwards-compatible single-installment payment: creates a one-line collection submitted for cash handover."""
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'installmentId')

        def create():
            inst = self._record('ranchi.loan.installment', data['installmentId'])
            loan = inst.loan_id
            amount = float(data.get('amount') or inst.amount_residual)
            journal_id = int(data['journalId']) if data.get('journalId') else \
                loan.company_id.ranchi_collection_journal_id.id
            if not journal_id:
                raise BadRequest("No journalId given and no default collection journal configured")
            collection = request.env['ranchi.collection'].create({
                'union_id': loan.union_id.id,
                'company_id': loan.company_id.id,
                'journal_id': journal_id,
                'date': data.get('date') or fields.Date.context_today(request.env.user),
                'line_ids': [(0, 0, {
                    'member_id': loan.member_id.id,
                    'loan_id': loan.id,
                    'amount_loan': amount,
                    'amount_savings': float(data.get('savingsAmount') or 0.0),
                })],
            })
            collection.action_submit()
            return {'collection': self._ser_collection(collection),
                    'installment': self._ser_installment(inst), 'loan': self._ser_loan(loan)}

        return self._run(lambda: self._idempotent(data, 'installments/pay', create))

    @api_route('/api/v1/collections')
    def collections(self, **kw):
        self._authenticate()
        data = self._params(kw)
        limit, offset = self._paginate(data)
        domain = []
        if data.get('unionId'):
            domain.append(('union_id', '=', int(data['unionId'])))
        if data.get('date'):
            domain.append(('date', '=', data['date']))
        if data.get('status'):
            domain.append(('state', '=', data['status']))
        cols = request.env['ranchi.collection'].search(domain, limit=limit, offset=offset)
        return [self._ser_collection(c) for c in cols]

    # ------------------------------------------------------------------
    # savings
    # ------------------------------------------------------------------
    @api_route('/api/v1/savings/balance/<int:member_id>')
    def savings_balance(self, member_id, **kw):
        self._authenticate()
        member = self._member(member_id)
        return {'memberId': member.id, 'total': self._money(member.savings_balance),
                'onHold': self._money(member.savings_on_hold), 'available': self._money(member.savings_available)}

    @api_route('/api/v1/savings/deposit')
    def savings_deposit(self, **kw):
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'memberId', 'amount')

        def create():
            member = self._member(data['memberId'])
            company = member.company_id or request.env.company
            journal_id = int(data['journalId']) if data.get('journalId') else company.ranchi_collection_journal_id.id
            if not journal_id:
                raise BadRequest("No journalId given and no default collection journal configured")
            tx = request.env['ranchi.savings.transaction'].create({
                'member_id': member.id,
                'company_id': company.id,
                'date': data.get('date') or fields.Date.context_today(request.env.user),
                'amount': float(data['amount']),
                'type': 'deposit',
                'origin': 'api',
                'journal_id': journal_id,
                'note': data.get('note'),
            })
            tx.action_post()
            return self._ser_savings_tx(tx)

        return self._run(lambda: self._idempotent(data, 'savings/deposit', create))

    @api_route('/api/v1/savings/withdrawal/request')
    def withdrawal_request(self, **kw):
        self._authenticate()
        data = self._params(kw)
        self._require(data, 'memberId', 'amount')

        def create():
            member = self._member(data['memberId'])
            req = request.env['ranchi.withdrawal.request'].create({
                'member_id': member.id,
                'company_id': (member.company_id or request.env.company).id,
                'amount': float(data['amount']),
                'reason': data.get('reason'),
                'request_date': data.get('date') or fields.Date.context_today(request.env.user),
            })
            if data.get('submit', True):
                req.action_submit()
            return self._ser_withdrawal(req)

        return self._run(lambda: self._idempotent(data, 'savings/withdrawal/request', create))

    @api_route('/api/v1/savings/withdrawals')
    def withdrawals(self, **kw):
        self._authenticate()
        data = self._params(kw)
        limit, offset = self._paginate(data)
        domain = []
        if data.get('memberId'):
            domain.append(('member_id', '=', int(data['memberId'])))
        if data.get('status'):
            domain.append(('state', '=', data['status']))
        reqs = request.env['ranchi.withdrawal.request'].search(domain, limit=limit, offset=offset)
        return [self._ser_withdrawal(w) for w in reqs]

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------
    @api_route('/api/v1/summary')
    def summary(self, **kw):
        self._authenticate()
        data = self._params(kw)
        today = fields.Date.context_today(request.env.user)
        Loan = request.env['ranchi.loan']
        Inst = request.env['ranchi.loan.installment']
        Col = request.env['ranchi.collection']
        loan_domain = [('union_id', '=', int(data['unionId']))] if data.get('unionId') else []
        inst_domain = list(loan_domain)
        disbursed = Loan.search(loan_domain + [('state', '=', 'disbursed')])
        collections_today = Col.search(loan_domain + [('date', '=', today), ('state', 'in', ('submitted', 'posted'))])
        awaiting = Col.search(loan_domain + [('state', '=', 'submitted')])
        return {
            'date': today.isoformat(),
            'activeLoans': len(disbursed),
            'completedLoans': Loan.search_count(loan_domain + [('state', '=', 'paid')]),
            'loansOutstanding': self._money(sum(disbursed.mapped('balance'))),
            'amountOverdue': self._money(sum(disbursed.mapped('amount_overdue'))),
            'loansWithArrears': len(disbursed.filtered('overdue_count')),
            'installmentsDueToday': Inst.search_count(
                inst_domain + [('date_due', '=', today), ('state', '!=', 'paid'), ('loan_state', '=', 'disbursed')]),
            'collectedToday': self._money(sum(collections_today.mapped('amount_total'))),
            'loanRepaidToday': self._money(sum(collections_today.mapped('amount_loan_total'))),
            'savingsCollectedToday': self._money(sum(collections_today.mapped('amount_savings_total'))),
            'awaitingHandover': len(awaiting),
            'awaitingHandoverAmount': self._money(sum(awaiting.mapped('amount_total'))),
            'pendingWithdrawals': request.env['ranchi.withdrawal.request'].search_count(
                loan_domain + [('state', 'in', ('submitted', 'approved_l1', 'approved'))]),
        }
