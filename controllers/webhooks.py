# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class RanchiWebhooks(http.Controller):
    """Payment gateway callbacks. Every call is logged before it is processed."""

    def _respond(self, status=200, body='ok'):
        return request.make_response(body, status=status, headers=[('Content-Type', 'text/plain')])

    def _log_and_process(self, provider, event_id, event_type, payload, company):
        Event = request.env['ranchi.gateway.event'].sudo()
        if Event.search_count([('provider', '=', provider), ('event_id', '=', event_id)]):
            _logger.info("%s webhook %s already received", provider, event_id)
            return self._respond()
        event = Event.create({
            'provider': provider,
            'event_id': event_id,
            'event_type': event_type,
            'payload': json.dumps(payload),
            'company_id': company.id if company else False,
        })
        event._process()
        return self._respond()

    @http.route('/ranchi/webhook/flutterwave', type='http', auth='public', methods=['POST'], csrf=False)
    def flutterwave(self, **kw):
        signature = request.httprequest.headers.get('verif-hash')
        if not signature:
            return self._respond(401, 'missing signature')
        companies = request.env['res.company'].sudo().search([])
        company = companies.filtered(
            lambda c: c.ranchi_flutterwave_webhook_hash and hmac.compare_digest(
                c.ranchi_flutterwave_webhook_hash, signature))[:1]
        if not company:
            _logger.warning("Flutterwave webhook with unknown signature")
            return self._respond(401, 'bad signature')
        raw = request.httprequest.get_data() or b''
        try:
            payload = json.loads(raw.decode() or '{}')
        except ValueError:
            return self._respond(400, 'invalid json')
        data = payload.get('data') or {}
        event_id = str(payload.get('id') or data.get('id') or hashlib.sha256(raw).hexdigest())
        event_type = payload.get('event') or payload.get('type') or ''
        return self._log_and_process('flutterwave', event_id, event_type, payload, company)

    @http.route('/ranchi/webhook/monnify', type='http', auth='public', methods=['POST'], csrf=False)
    def monnify(self, **kw):
        signature = request.httprequest.headers.get('monnify-signature')
        if not signature:
            return self._respond(401, 'missing signature')
        raw = request.httprequest.get_data() or b''
        companies = request.env['res.company'].sudo().search([('ranchi_monnify_secret_key', '!=', False)])
        company = None
        for candidate in companies:
            digest = hmac.new(candidate.ranchi_monnify_secret_key.encode(), raw, hashlib.sha512).hexdigest()
            if hmac.compare_digest(digest, signature):
                company = candidate
                break
        if not company:
            _logger.warning("Monnify webhook with unknown signature")
            return self._respond(401, 'bad signature')
        try:
            payload = json.loads(raw.decode() or '{}')
        except ValueError:
            return self._respond(400, 'invalid json')
        data = payload.get('eventData') or {}
        event_id = str(data.get('transactionReference') or data.get('reference') or hashlib.sha256(raw).hexdigest())
        event_type = payload.get('eventType') or ''
        return self._log_and_process('monnify', event_id, event_type, payload, company)
