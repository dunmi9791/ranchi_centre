# Ranchi Centre (`ranchi_centre`)

Odoo 18 module for Ranchi Empowerment Centre. It replaces the three legacy repos
`ranchi_loanee_management`, `ranchi_savings_management` and `ranchi_apis` with one
application. Background and design rationale: `../docs/legacy_modules_review.md`.

## What it covers

| Area | Model(s) | Notes |
|---|---|---|
| Branch settings | `res.company`, `res.config.settings` | Accounts, journals, fee products, savings rules, gateway credentials |
| Dashboard | `ranchi.dashboard` (abstract) + OWL client action | Unions, members, active loans, savings, portfolio at risk, today's work, pending approvals, six-month chart, arrears by union. Read as the current user, so officers see only their unions |
| Unions | `ranchi.union` | Credit officer (hr.employee), the one loan type the union runs, union day (weekly types only: daily types such as Rapid have none), member list, outstanding totals |
| Members | `res.partner` | Applied -> Vetted -> Confirmed -> Exited, member number, KYC, savings balances |
| Loan config | `ranchi.loan.type`, `ranchi.loan.stage` | Service charge, admin fee, risk premium, installments, cycle ladder |
| Loans | `ranchi.loan`, `ranchi.loan.installment`, `ranchi.loan.repayment` | Draft -> Applied -> Approved -> Fees Paid -> Disbursed -> Fully Paid / Written Off |
| Disbursement | `ranchi.disbursement`, `ranchi.gateway.event` | Manual, Flutterwave or Monnify; idempotent webhook log |
| Savings | `ranchi.savings.transaction`, `ranchi.savings.rate` | Deposits, withdrawals, interest, fees, holds on a liability account |
| Withdrawals | `ranchi.withdrawal.request` | One or two approval levels, hold, fee, payout wizard |
| Collections | `ranchi.collection`, `ranchi.collection.line` | Union-day event: repayments + savings in one journal entry |
| Savings adjustment | `ranchi.lapse.adjustment` | Pay a loan from savings: officer requests (savings held), the loan product's manager confirms and posts; optional settle-in-full with cash shortfall |
| API | `controllers/api.py` | `/api/v1/...` JSON-RPC, bearer API key, idempotency keys |
| Portal | `/my/savings`, `/my/loans` | Members see balances, transactions, schedules |
| Report | Savings statement | Wizard with date range, running balance |
| Report | Portfolio analysis | `ranchi.portfolio.report` SQL view, one row per disbursed loan: outstanding, arrears, days in arrears, PAR >0/30/90 by branch, credit officer, union (pivot/graph/list) |
| Report | Credit officer portfolio / Portfolio at risk | PDF wizard: per officer every union with its active loans and write-offs; PAR summary by branch, officer and union |

## Accounting design

* **Disbursement**: Dr Loans Receivable (one line per installment, partner, maturity date) /
  Cr Bank (principal) / Cr Service Charge Income (when spread). The receivable account must
  allow reconciliation.
* **Repayment** (collection, lapse, API, write-off): Cr Loans Receivable per installment,
  reconciled against the disbursement line. Installment residual and loan balance come from
  `amount_residual`, so Fully Paid is reached automatically.
* **Fees**: customer invoices (membership, admin fee, risk premium, up-front service charge)
  on the configured sales journal. The loan moves to Fees Paid when the invoice is paid.
* **Field fees**: each loan type lists its registration fees, the savings a new member needs
  before approval, and other fees due before disbursement (fixed or % of the approved amount).
  Credit officers collect them on a collection line (`amount_fees`). Posting the collection
  credits the member's receivable and reconciles it with the registration invoice first, then
  the approved loan's fee invoice (each created if missing). A fully paid registration invoice
  vets the member (auto-vet setting); a fully paid loan fee invoice moves the loan to Fees Paid.
  A member is only confirmed once the loan type's registration fees are paid and the savings
  balance reaches the minimum. Without registration fees on the loan type, the branch
  membership fee applies and does not block confirmation.
* **Savings**: deposit Dr Bank / Cr Savings Liability (partner); withdrawal the reverse;
  interest Dr Interest Expense / Cr Liability; fee Dr Liability / Cr Fee Income. Member
  balances are read from posted lines on the liability account. Holds are non-accounting.

## Setup checklist

1. Install the module. `post_init_hook` points every company at the four fee products.
2. Ranchi Centre > Configuration > Settings: set the six accounts and four journals, the
   membership fee, savings rules and the disbursement provider.
3. Create loan types (and stages) with their meeting frequency and field fees (Field Fees tab), a savings interest rate, then unions with a loan type and credit officer. A union's loan type cannot change while it has active loans.
4. Give users a role: Credit Officer, Accountant, Branch Manager or Auditor. A credit
   officer must be linked to an `hr.employee` with a user, and be set on their unions.
5. For the mobile app (`../ranchi_officer`): the officer signs in with their Odoo login; the app
   calls `/api/v1/auth/login`, receives a persistent device API key (visible under Preferences >
   Account Security) and sends `Authorization: Bearer <key>` from then on. `/api/v1/auth/logout`
   revokes it.
6. Gateways: configure the webhook URL `/ranchi/webhook/flutterwave` (verif-hash) or
   `/ranchi/webhook/monnify` (HMAC-SHA512 of the body) in the provider dashboard.
7. Enable the cron "Ranchi: monthly savings interest accrual" if interest is paid.

## API v1 (JSON-RPC 2.0, POST)

`/api/v1/auth/login`, `/api/v1/auth/logout`, `/api/v1/me`, `/api/v1/unions`, `/api/v1/unions/<id>`, `/api/v1/unions/<id>/members`,
`/api/v1/unions/<id>/installments`, `/api/v1/members`, `/api/v1/members/<id>`,
`/api/v1/members/create`, `/api/v1/members/<id>/loans`, `/api/v1/members/<id>/savings`,
`/api/v1/loantypes`, `/api/v1/loans`, `/api/v1/loans/<id>`, `/api/v1/loans/create`,
`/api/v1/installments/today|overdue|upcoming`, `/api/v1/installments/pay`,
`/api/v1/collections`, `/api/v1/collections/create`, `/api/v1/payment_journals`,
`/api/v1/savings/balance/<id>`, `/api/v1/savings/deposit`, `/api/v1/savings/withdrawal/request`,
`/api/v1/savings/withdrawals`, `/api/v1/savings/adjustment/request`, `/api/v1/savings/adjustments`,
`/api/v1/savings/adjustment/<id>/cancel`, `/api/v1/summary`.

Write endpoints accept `idempotencyKey`. Requests run as the key's user, so credit officers
only see their unions.

Field fees: members carry `feesDue`, `fees` (`kind` registration/loan, `name`, `due`),
`savingsRequired` and `savingsDue`, net of collections handed over but not posted yet. Loan
types carry `registrationFees`, `registrationSavings` and `disbursementFees`. Collection lines
take `feesAmount`. `members/create` takes `collect: {feesAmount, savingsAmount, journalId?, date?}`
and returns the member with the submitted registration `collection` (origin `registration`);
member and collection are created together or not at all.

## Tests

```bash
odoo-bin -d <db> -i ranchi_centre --test-tags /ranchi_centre --stop-after-init
```
