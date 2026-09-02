# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

SaldoVibe is a Django 5.2 LTS accounting app for Swedish businesses. Swedish is the UI language and
domain vocabulary throughout (`verifikation` = voucher, `konto` = account, `räkenskapsår` =
accounting year, `momsdeklaration` = VAT return). Targets Bokföringslagen/Skatteverket compliance
(`SKATTEVERKET_COMPLIANCE_PLAN.md`, `docs/compliance/`).

## Commands

Run everything through the venv (`.venv/bin/...`).

```bash
# Setup
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.lock   # hash-verified; matches prod image
cp .env.example .env

python manage.py runserver

# Lint / format (CI enforces both)
ruff check .
ruff format --check .          # `ruff format .` to fix
python scripts/check_dockerfile_apps.py     # Dockerfile COPYs every app?
python scripts/check_requirements_lock.py   # lockfiles stale?

# Tests (app / module / single test work as usual)
python manage.py test
python manage.py test bookkeeping.tests.test_period_locks

# Migrations
python manage.py makemigrations <app> && python manage.py migrate
python manage.py makemigrations --check --dry-run   # no unmigrated model changes

# Required after adding/renaming anything under static/
python manage.py collectstatic --noinput
```

- **Never commit directly to `main`.** Every task gets its own branch (`git switch -c <slug>` from
  an up-to-date `main`); changes land on `main` via PR.
- Git hooks (opt-in): `git config core.hooksPath .githooks` — ruff on commit, full suite on push
  (`git push --no-verify` skips once).
- **Dependencies are locked.** Changed `requirements*.txt`? Use the `update-dependencies` skill to
  regenerate the `.lock` files; CI fails on drift.
- **Python 3.13 everywhere** (venv, Dockerfile, CI) — keep in sync. Never 3.14:
  `rfc3161ng` (audit timestamping) and `xhtml2pdf` (PDF reports) don't support it, both are
  compliance-critical. 3.13 outlives Django 5.2 LTS, so no reason to move.

### Docker

One stack: `docker-compose.yml` + `Dockerfile` — Postgres + nginx + Gunicorn + WhiteNoise +
ofelia scheduler. It reads `.env` (copy from `.env.example`; same file feeds compose
interpolation, container env, and a bare `manage.py runserver` via python-dotenv). Local dev runs
without Docker. Variable reference: `docs/ops/environment-variables.md`.

## Lint & Test Gate

Before declaring any task done, all three must pass: `ruff check .`, `ruff format --check .`, and
the full `python manage.py test`. Don't stop after `ruff check` — `format --check` catches things
it doesn't.

## Verification Policy

UI/JS changes aren't done until verified in a real browser (headless screenshot or DOM assertion) —
disabled buttons, broken iframes, and JS load-order bugs have all shipped green through the test
suite. Never write throwaway data into the dev database; use test fixtures or a disposable sqlite
file.

## User Documentation

`docs/user-guide/` is the Swedish end-user manual, rendered live at `/hjalp/`. A change that alters
what a chapter describes updates the chapter **in the same change** — conventions in
`.claude/rules/user-guide.md`.

## Architecture

### App layout (multi-tenant, company-scoped)

- `accounts` — custom user model (`AUTH_USER_MODEL = accounts.CustomUser`).
- `bookkeeping` — core: `Company`, `Account` (BAS), `AccountingYear`, `PeriodLock`,
  `Transaction`/`JournalEntry` (double-entry vouchers), voucher series, SIE, reports, PDF.
- `auditlog` — hash-chained append-only audit log (below).
- `attachments`, `supplier_invoices`, `expenses`, `banking`, `invoicing`, `payroll`, `vat`,
  `fixed_assets` — each posts journal entries into `bookkeeping` via its own `services.py`.

Every business model belongs to a `Company`. Tenant resolution: `bookkeeping.company_scope` —
`get_active_company(request)` (session `active_company_id`, scoped to `Company.objects.filter(users=user)`,
superusers see all), the `@require_company` decorator (injects company as second view arg), and the
`active_company` context processor. Scope all queries and permission checks through the active
`Company` — no cross-tenant views except for superusers.

### Payables (`bookkeeping/payables.py`)

Shared payment-state and öresavrundning write-off logic for all invoice-like documents — see
`.claude/rules/payables.md` (auto-loads when working in the relevant apps).

### Compliance-driven pieces

- `bookkeeping/period_locking.py` — `PeriodLock` blocks edits in locked ranges (BFNAR 2013:2).
  Enforce before any write to a period-scoped model.
- `bookkeeping/compliance_policy.py` — role gating (`finance_operator` < `finance_admin` <
  `system_admin`, or staff/superuser) via `ACTION_ROLE_MATRIX`; check before adding privileged
  actions.
- `bookkeeping/bas_accounts.py` — BAS chart from `bookkeeping/data/bas_2026_accounts.json`;
  4-digit account numbers, VAT codes validated against `Account.VatFieldCode`.
- `bookkeeping/sie.py` / `sie_import.py` — SIE export/import.
- `payroll/skatteverket_api.py`, `payroll/agi.py` — Skatteverket API + AGI reporting.
- `invoicing/peppol.py` — Peppol e-invoicing.

### Audit log (`auditlog`)

Every write to a tracked model becomes an immutable, hash-chained ledger entry. New auditable
model → register in `TRACKED_MODELS` (`auditlog/services.py`). Details in
`.claude/rules/auditlog.md`.

### Storage / runtime paths

`saldovibe/runtime.py` resolves `BASE_DIR`/`DATA_DIR` (source vs frozen vs `SALDOVIBE_DATA_DIR`).
Sqlite DB, media, and static all live under `DATA_DIR` — never hardcode runtime-writable paths
relative to `BASE_DIR`.

### Frontend

Server-rendered Django templates + Bootstrap 5, Chart.js, Select2, jQuery — vendored via
`node_modules` through `STATICFILES_DIRS`, no bundler. JS conventions, the WhiteNoise
static-manifest gotcha (`npm ci` + `collectstatic` before tests), and template rendering rules:
`.claude/rules/frontend.md`.

## Reuse Before Building

Grep before writing anything generic-feeling. Confirmed shared building blocks:

- `static/js/attachment-picker.js` — `SaldoVibe.attachments.initSelectionList`,
  `initFormStateRoundtrip` (sessionStorage form stash around the picker),
  `initUploadLoadingState` (spinner during upload POST — ReInvGrabber OCR runs synchronously in
  that request, see `attachments/extraction_client.py`). Used by all document-entry forms.
- `templates/attachments/attachment_picker.html` / `_attachment_panel.html` — picker page +
  preview panel (listens for `attachment-viewer:show`/`remove` CustomEvents from
  `initSelectionList({ viewerEvents: true })`).
- `attachments.utils.is_safe_return_to` — the `return_to` safe-redirect check.
- `bookkeeping/balances.py` — shared account-balance calculation.
- `bookkeeping/payables.py` — payment state + öresavrundning write-offs.
- `bookkeeping/forms.py::ManualPaymentForm` — the payment-date form behind every "markera som
  betald" action.

## Cross-App Change Checklist

Attachment handling, `return_to` redirects, and payment/booking logic are mirrored across
`supplier_invoices`, `invoicing`, `expenses`, `banking`, and `bookkeeping` manual verifications —
when changing a shared pattern, fix every occurrence in the same pass. The list of
already-consolidated pieces (fix once, not per app) is in `.claude/rules/cross-app.md`.
