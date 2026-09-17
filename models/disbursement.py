# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
GATEWAY_TIMEOUT = 20


class RanchiDisbursement(models.Model):
    _name = 'ranchi.disbursement'
    _description = 'Ranchi Loan Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string="Reference", readonly=True, copy=False, default=lambda self: _('New'),
                       help="Sent to the gateway as the idempotent transfer reference.")
    loan_id = fields.Many2one('ranchi.loan', required=True, ondelete='restrict', index=True)
    member_id = fields.Many2one(related='loan_id.member_id', store=True)
    union_id = fields.Many2one(related='loan_id.union_id', store=True)
    company_id = fields.Many2one(related='loan_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='loan_id.currency_id')
    amount = fields.Monetary(required=True, tracking=True)
    journal_id = fields.Many2one(
        'account.journal', string="Disbursement Journal", check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    bank_account_id = fields.Many2one(
        'res.partner.bank', string="Beneficiary Account",
        domain="[('partner_id', '=', member_id)]")
    account_number = fields.Char(related='bank_account_id.acc_number')
    bank_name = fields.Char(related='bank_account_id.bank_id.name')
    bank_code = fields.Char(
        string="Bank Code", compute='_compute_bank_code', store=True, readonly=False,
        help="Gateway bank code (Flutterwave/Monnify). Defaults to the bank's BIC field.")
    provider = fields.Selection(
        [('manual', 'Manual bank transfer'), ('flutterwave', 'Flutterwave'), ('monnify', 'Monnify')],
        required=True, default='manual', tracking=True)
    state = fields.Selection(
        [('pending', 'Pending'), ('processing', 'Processing'), ('done', 'Paid Out'),
         ('failed', 'Failed'), ('cancelled', 'Cancelled')],
        default='pending', required=True, tracking=True, copy=False, index=True)
    provider_reference = fields.Char(string="Gateway Reference", readonly=True, copy=False)
    provider_status = fields.Char(string="Gateway Status", readonly=True, copy=False)
    error_message = fields.Text(readonly=True, copy=False)
    date_requested = fields.Datetime(default=fields.Datetime.now, readonly=True)
    date_sent = fields.Datetime(readonly=True, copy=False)
    date_processed = fields.Datetime(readonly=True, copy=False)
    move_id = fields.Many2one(related='loan_id.disbursement_move_id', string="Journal Entry")
    narration = fields.Char(compute='_compute_narration', store=True, readonly=False)
    event_ids = fields.One2many('ranchi.gateway.event', 'disbursement_id', string="Gateway Events")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ranchi.disbursement') or '/'
        return super().create(vals_list)

    @api.depends('bank_account_id.bank_id.bic')
    def _compute_bank_code(self):
        for rec in self:
            if not rec.bank_code:
                rec.bank_code = rec.bank_account_id.bank_id.bic or False

    @api.depends('loan_id.name', 'member_id.name')
    def _compute_narration(self):
        for rec in self:
            if not rec.narration:
                rec.narration = _("Loan %(loan)s for %(member)s", loan=rec.loan_id.name, member=rec.member_id.name)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_send(self):
        for rec in self:
            if rec.state not in ('pending', 'failed'):
                raise UserError(_("Only pending or failed disbursements can be sent."))
            if rec.loan_id.state != 'fees':
                raise UserError(_("Loan %s is not ready for disbursement.", rec.loan_id.name))
            if rec.provider != 'manual' and (not rec.account_number or not rec.bank_code):
                raise UserError(_("A beneficiary account number and bank code are required for %s.", rec.provider))
            rec.write({'state': 'processing', 'date_sent': fields.Datetime.now(), 'error_message': False})
            if rec.provider == 'manual':
                rec.message_post(body=_("Marked as sent for manual bank transfer. Mark it paid out once the transfer clears."))
                continue
            try:
                getattr(rec, f'_send_{rec.provider}')()
            except Exception as exc:  # noqa: BLE001 - surface every gateway failure on the record
                _logger.exception("Disbursement %s failed", rec.name)
                rec._process_failure(str(exc))

    def action_mark_done(self):
        """Manual confirmation that the money left the bank."""
        for rec in self:
            if rec.state not in ('pending', 'processing'):
                raise UserError(_("Only pending or processing disbursements can be marked as paid out."))
            if rec.provider != 'manual' and not self.env.user.has_group('ranchi_centre.group_ranchi_manager'):
                raise UserError(_("Only a branch manager may override a gateway disbursement."))
            rec._process_success()

    def action_mark_failed(self):
        for rec in self:
            if rec.state not in ('pending', 'processing'):
                raise UserError(_("Only pending or processing disbursements can be marked as failed."))
            rec._process_failure(_("Marked as failed by %s", self.env.user.name))

    def action_retry(self):
        for rec in self:
            if rec.state != 'failed':
                raise UserError(_("Only failed disbursements can be retried."))
            rec.write({'state': 'pending', 'error_message': False})

    def action_cancel(self):
        for rec in self:
            if rec.state not in ('pending', 'failed'):
                raise UserError(_("Only pending or failed disbursements can be cancelled."))
            rec.write({'state': 'cancelled'})

    def _process_success(self, provider_reference=None, provider_status=None):
        for rec in self:
            if rec.state == 'done':
                continue
            if rec.state == 'cancelled':
                raise UserError(_("Disbursement %s was cancelled and cannot be completed.", rec.name))
            vals = {'state': 'done', 'date_processed': fields.Datetime.now()}
            if provider_reference:
                vals['provider_reference'] = provider_reference
            if provider_status:
                vals['provider_status'] = provider_status
            rec.write(vals)
            rec.loan_id._on_disbursed(rec)

    def _process_failure(self, message, provider_status=None):
        for rec in self:
            if rec.state == 'done':
                continue
            rec.write({
                'state': 'failed',
                'error_message': message,
                'provider_status': provider_status or rec.provider_status,
                'date_processed': fields.Datetime.now(),
            })
            rec.message_post(body=_("Disbursement failed: %s", message))

    # ------------------------------------------------------------------
    # Gateways
    # ------------------------------------------------------------------
    def _send_flutterwave(self):
        self.ensure_one()
        company = self.company_id.sudo()
        secret = company.ranchi_flutterwave_secret_key
        if not secret:
            raise UserError(_("Flutterwave secret key is not configured."))
        payload = {
            'account_bank': self.bank_code,
            'account_number': self.account_number,
            'amount': self.amount,
            'currency': self.currency_id.name,
            'narration': self.narration,
            'reference': self.name,
            'debit_currency': self.currency_id.name,
        }
        response = requests.post(
            'https://api.flutterwave.com/v3/transfers', json=payload,
            headers={'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'},
            timeout=GATEWAY_TIMEOUT)
        data = response.json() if response.content else {}
        if response.ok and data.get('status') == 'success':
            body = data.get('data') or {}
            self.write({
                'provider_reference': str(body.get('id') or ''),
                'provider_status': body.get('status'),
            })
            if body.get('status') == 'SUCCESSFUL':
                self._process_success()
            elif body.get('status') == 'FAILED':
                self._process_failure(body.get('complete_message') or _('Transfer failed'))
        else:
            raise UserError(data.get('message') or response.text or _('Flutterwave rejected the transfer.'))

    def _monnify_token(self, company):
        response = requests.post(
            f"{company.ranchi_monnify_base_url.rstrip('/')}/api/v1/auth/login",
            auth=(company.ranchi_monnify_api_key or '', company.ranchi_monnify_secret_key or ''),
            timeout=GATEWAY_TIMEOUT)
        data = response.json() if response.content else {}
        token = (data.get('responseBody') or {}).get('accessToken')
        if not token:
            raise UserError(_("Monnify authentication failed: %s", data.get('responseMessage') or response.text))
        return token

    def _send_monnify(self):
        self.ensure_one()
        company = self.company_id.sudo()
        if not company.ranchi_monnify_api_key or not company.ranchi_monnify_secret_key:
            raise UserError(_("Monnify credentials are not configured."))
        if not company.ranchi_monnify_source_account:
            raise UserError(_("Monnify source account is not configured."))
        token = self._monnify_token(company)
        payload = {
            'amount': self.amount,
            'reference': self.name,
            'narration': self.narration,
            'destinationBankCode': self.bank_code,
            'destinationAccountNumber': self.account_number,
            'currency': self.currency_id.name,
            'sourceAccountNumber': company.ranchi_monnify_source_account,
        }
        response = requests.post(
            f"{company.ranchi_monnify_base_url.rstrip('/')}/api/v2/disbursements/single",
            json=payload, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=GATEWAY_TIMEOUT)
        data = response.json() if response.content else {}
        if response.ok and data.get('requestSuccessful'):
            body = data.get('responseBody') or {}
            status = body.get('status')
            self.write({'provider_reference': body.get('reference') or self.name, 'provider_status': status})
            if status == 'SUCCESS':
                self._process_success()
            elif status == 'FAILED':
                self._process_failure(body.get('message') or _('Transfer failed'))
        else:
            raise UserError(data.get('responseMessage') or response.text or _('Monnify rejected the transfer.'))

    def _action_open(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ranchi.disbursement',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class RanchiGatewayEvent(models.Model):
    _name = 'ranchi.gateway.event'
    _description = 'Ranchi Payment Gateway Webhook Event'
    _order = 'id desc'

    provider = fields.Selection(
        [('flutterwave', 'Flutterwave'), ('monnify', 'Monnify')], required=True, index=True)
    event_id = fields.Char(string="Event ID", required=True, index=True)
    event_type = fields.Char()
    payload = fields.Text()
    state = fields.Selection(
        [('received', 'Received'), ('processed', 'Processed'), ('ignored', 'Ignored'), ('error', 'Error')],
        default='received', required=True, index=True)
    error_message = fields.Text()
    disbursement_id = fields.Many2one('ranchi.disbursement', ondelete='set null')
    company_id = fields.Many2one('res.company')

    _sql_constraints = [
        ('provider_event_uniq', 'unique(provider, event_id)', 'This gateway event was already received.'),
    ]

    def action_reprocess(self):
        self.write({'state': 'received', 'error_message': False})
        self._process()
        return True

    def _process(self):
        for event in self:
            try:
                payload = json.loads(event.payload or '{}')
                handler = getattr(event, f'_process_{event.provider}')
                handler(payload)
            except Exception as exc:  # noqa: BLE001
                _logger.exception("Gateway event %s failed", event.id)
                event.write({'state': 'error', 'error_message': str(exc)})

    def _find_disbursement(self, reference):
        if not reference:
            return self.env['ranchi.disbursement']
        return self.env['ranchi.disbursement'].search([('name', '=', reference)], limit=1)

    def _process_flutterwave(self, payload):
        self.ensure_one()
        event_type = payload.get('event') or payload.get('type') or ''
        data = payload.get('data') or {}
        if not event_type.startswith('transfer'):
            self.write({'state': 'ignored'})
            return
        disbursement = self._find_disbursement(data.get('reference'))
        if not disbursement:
            self.write({'state': 'ignored', 'error_message': _('No disbursement matches the reference.')})
            return
        self.disbursement_id = disbursement
        status = (data.get('status') or '').upper()
        if status == 'SUCCESSFUL':
            disbursement._process_success(provider_reference=str(data.get('id') or ''), provider_status=status)
        elif status == 'FAILED':
            disbursement._process_failure(data.get('complete_message') or _('Transfer failed'), provider_status=status)
        else:
            disbursement.write({'provider_status': status})
        self.write({'state': 'processed'})

    def _process_monnify(self, payload):
        self.ensure_one()
        event_type = payload.get('eventType') or ''
        data = payload.get('eventData') or {}
        if 'DISBURSEMENT' not in event_type.upper():
            self.write({'state': 'ignored'})
            return
        disbursement = self._find_disbursement(data.get('reference'))
        if not disbursement:
            self.write({'state': 'ignored', 'error_message': _('No disbursement matches the reference.')})
            return
        self.disbursement_id = disbursement
        status = (data.get('status') or '').upper()
        if event_type.upper() == 'SUCCESSFUL_DISBURSEMENT' or status == 'SUCCESS':
            disbursement._process_success(provider_reference=data.get('transactionReference'), provider_status=status)
        elif event_type.upper() == 'FAILED_DISBURSEMENT' or status == 'FAILED':
            disbursement._process_failure(data.get('narration') or _('Transfer failed'), provider_status=status)
        else:
            disbursement.write({'provider_status': status})
        self.write({'state': 'processed'})
