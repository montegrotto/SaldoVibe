# Logging & monitoring

## Where logs go

There is no file-based or external log shipping configured — everything logs to **stdout/stderr**
via a single console handler (`saldovibe/settings.py`, `LOGGING`), which is the right shape for a
container: let the container runtime/host collect it.

```bash
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f db
docker compose -f docker-compose.prod.yml logs -f nginx
```

Gunicorn (the `web` process) is started with `--access-logfile - --error-logfile -`
(`Dockerfile` CMD), so HTTP access logs and error logs both land in the same stream as Django's
own logging.

## Log levels and loggers

| Logger | Level | Notes |
|---|---|---|
| root / `django` | `INFO` | Framework-level messages. |
| `django.request` | `WARNING` | 4xx/5xx request errors — this is what surfaces broken views. |
| `attachments` | `INFO` | Attachment upload/delete/thumbnail events. |
| `attachments.email_import` | `DEBUG` in dev, `INFO` in prod | Email-fetch import runs (Gmail/Outlook) — see [user guide, chapter 4](../user-guide/04-bilagor.md). |
| `bookkeeping` | `INFO` | Company/account/transaction lifecycle events (creation, deletion attempts, SIE import results). |

Most application views also call `messages.error/success/...` for user-facing feedback *and*
`logger.info`/`logger.warning`/`logger.exception` for the same event — so the container logs are a
reasonable audit trail of "what happened and to which company/user" even without a dedicated log
aggregator. Structured fields are passed via the `extra={...}` kwarg (company_id, user_id, and
action-specific detail), which is compatible with most log processors that can parse structured
`extra` fields (e.g. via `python-json-logger`) if you add one later.

## What to actually watch in production

Since there's no dashboard/alerting shipped with the app itself, treat these as the manual (or
lightly scripted) checks worth doing periodically, beyond generic container health:

- **`django.request` WARNING/ERROR entries** — recurring 500s on the same view is the first sign
  something is broken for users.
- **Repeated "perioden är låst" / "inte i balans" messages** in `bookkeeping` logs — could mean a
  user is stuck on a real workflow problem, not just a validation nag.
- **`attachments.email_import` failures** — a broken IMAP/OAuth credential silently stops pulling
  bilagor in; nothing else will surface this except the log line and the in-app warning message
  shown once at config time.
- **Skatteverket API errors during payroll finish** — since tax lookup failures hard-block
  finishing a payroll run (see [environment-variables.md](environment-variables.md)), a spike here
  means payroll is stuck company-wide until the API or credentials are fixed.

For the compliance-specific signals (voucher numbering gaps, late postings, orphaned attachments,
audit hash-chain mismatches), use the in-app **Compliance-översikt**
([user guide, chapter 10](../user-guide/10-rapporter.md)) rather than grepping logs — it's built
for exactly this.

## No external monitoring is wired up

There is currently no APM, uptime check, or metrics exporter in the stack. If/when that becomes
worth adding, the natural integration points are:

- A `django.request` log-based alert (already structured enough to threshold on).
- An HTTP health check against `/` for uptime monitoring (nginx already proxies `/` straight to
  `web`; there's no dedicated `/healthz` endpoint today).
- Postgres's own `pg_isready`, already used as the Compose healthcheck for `db` — reuse that check
  for external uptime monitoring too instead of inventing a new one.
