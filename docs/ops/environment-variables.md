# Environment variables

Consolidated reference for every environment variable the running app reads. This is the single
source of truth — the root `README.md` links here instead of duplicating the list.

Values are read in `saldovibe/settings.py` unless noted otherwise.

Docker Compose sets these via each stack's `env_file` (see root `README.md`). Outside Docker,
`manage.py` also auto-loads the root `.env` via `python-dotenv` (without overriding variables
already present in the environment), so a bare `python manage.py runserver` picks up the same
file as the standalone `docker-compose.yml` profile — except where that profile hardcodes a
variable itself (e.g. `DJANGO_DEBUG: "0"`), which always wins for that stack regardless of `.env`.

## Core Django / networking

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `0` | Django debug mode. Must be `0` in production. |
| `DJANGO_SECRET_KEY` | insecure public fallback | Django's `SECRET_KEY`. Must be set to a generated value in production. |
| `SALDOVIBE_FIELD_ENCRYPTION_KEY` | derived from `SECRET_KEY` | Fernet key for field encryption at rest (personnummer). Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Set it explicitly in production — rotating `DJANGO_SECRET_KEY` without this set makes encrypted fields unreadable. |
| `SALDOVIBE_PUBLIC_URL` | (empty) | Canonical browser-facing base URL, e.g. `https://bokforing.example.se`. Used to derive `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` when the explicit override vars below are not set. |
| `DJANGO_ALLOWED_HOSTS` | derived from `SALDOVIBE_PUBLIC_URL`, else `127.0.0.1,localhost,[::1]` | Comma-separated explicit override for `ALLOWED_HOSTS`. Use for multi-domain deployments. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | derived from `SALDOVIBE_PUBLIC_URL` | Comma-separated explicit override for `CSRF_TRUSTED_ORIGINS`. Use for multi-origin deployments. |
| `DJANGO_USE_X_FORWARDED_HOST` | `1` (true) | Whether to trust `X-Forwarded-Host` from a reverse proxy. |
| `DJANGO_USE_SECURE_PROXY_SSL_HEADER` | `1` (true) | Whether to trust `X-Forwarded-Proto` from a reverse proxy to detect HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `0` (false) | Set to `1` once the app is only ever served over HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `0` (false) | Set to `1` once the app is only ever served over HTTPS. |

Boolean-style flags accept `1`, `true`, `True`, `yes`, or `on`; anything else is treated as false.

## Runtime data locations

| Variable | Default | Purpose |
|---|---|---|
| `SALDOVIBE_DATA_DIR` | project root | Base directory for sqlite database, uploaded media, and derived runtime files. |
| `SALDOVIBE_STATIC_ROOT` | `<data dir>/staticfiles` | Collected static files directory (`collectstatic` target). |

## Database

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_ENGINE` | `django.db.backends.sqlite3` | Set to `django.db.backends.postgresql` for production. Any other value activates the full Postgres-style connection settings below. |
| `DATABASE_NAME` | `saldovibe` | Postgres database name. |
| `DATABASE_USER` | `saldovibe` | Postgres user. |
| `DATABASE_PASSWORD` | (empty) | Postgres password. |
| `DATABASE_HOST` | `localhost` | Postgres host (`db` in the Docker Compose network). |
| `DATABASE_PORT` | `5432` | Postgres port. |

The `db` service in `docker-compose.prod.yml` additionally reads `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` (standard `postgres` image init variables) — keep these in sync with the
`DATABASE_*` values above, since both come from the same `.env.prod` file.

## Container entrypoint

| Variable | Default | Purpose |
|---|---|---|
| `SALDOVIBE_RUN_MIGRATIONS` | `1` | When `1`, `scripts/docker-entrypoint.sh` runs `manage.py migrate --noinput` on every container start before handing off to the CMD (gunicorn). Set to `0` to skip, e.g. if migrations are run out-of-band. See [upgrades-migrations.md](upgrades-migrations.md). |

## Skatteverket API (payroll preliminary tax lookups)

| Variable | Default | Purpose |
|---|---|---|
| `SKATTEVERKET_API_BASE_URL` | public Entryscape dataset URL | Base URL for the tax-table lookup API used when finishing a payroll run. |
| `SKATTEVERKET_API_TAX_PATH` | (empty) | Optional path override appended to the base URL. |
| `SKATTEVERKET_API_KEY` | (empty) | API key, if the configured endpoint requires one. |
| `SKATTEVERKET_API_BEARER_TOKEN` | (empty) | Bearer token, if the configured endpoint requires one. |
| `SKATTEVERKET_API_TIMEOUT` | `12` (seconds) | Request timeout for the tax lookup call. |
| `SKATTEVERKET_API_STRICT` | `0` (false) | When true, treats lookup failures as hard errors everywhere they occur, not just on payroll finish. |
| `SKATTEVERKET_CA_BUNDLE` | (empty) | Path to a custom CA bundle, for environments with custom TLS interception. |
| `SKATTEVERKET_USE_CERTIFI` | `1` (true) | Use the bundled `certifi` CA bundle unless a custom one is provided. |

## System email (notification digest)

Outgoing email for **system notifications** (the daily digest sent by `skicka_notisdigest`, see
`bookkeeping/outgoing_mail.py`). Customer-facing email (invoices, payment reminders) is sent via
each company's own account configured in the company settings UI, not these variables — and a
company can also override the notification sender there ("Notiser till användare": own SMTP
account, Microsoft 365 mailbox, or the company's outgoing account), in which case these variables
are not used for that company. With `EMAIL_HOST` empty, Django's console backend is used — mails
are written to the log instead of sent (dev default, and a safe state for unconfigured
production).

| Variable | Default | Purpose |
|---|---|---|
| `EMAIL_HOST` | (empty) | SMTP server for system mail. Empty = console backend, nothing is sent. |
| `EMAIL_PORT` | `587` | SMTP port. |
| `EMAIL_HOST_USER` | (empty) | SMTP username. |
| `EMAIL_HOST_PASSWORD` | (empty) | SMTP password. |
| `EMAIL_USE_TLS` | `1` (true) | STARTTLS on the connection. |
| `DEFAULT_FROM_EMAIL` | `saldovibe@localhost` | From address on system mail. |

## ReInvGrabber (attachment field extraction)

Runs in-process (see `attachments/extraction_client.py` and the `reinvgrabber-extraction`
dependency in `requirements.txt`, sourced from https://github.com/montegrotto/ReInvGrabber) — no
separate service or URL to configure. Needs the Tesseract OCR binary + Swedish language data on
`PATH`; already installed in both Dockerfiles.

| Variable | Default | Purpose |
|---|---|---|
| `REINVGRABBER_ENABLED` | `1` (true) | Set to `0` to disable extraction entirely — attachment upload behaves exactly as before, no OCR runs. |

Finishing a payroll run ([user guide, chapter 6](../user-guide/06-loner.md)) calls this API
synchronously and **hard-fails the whole payroll close** if the call doesn't succeed — there is no
silent fallback tax calculation, by design.

## Where these are set in practice

- **Standalone Docker (no Postgres/nginx)**: `.env` (git-ignored, loaded via `env_file` in
  `docker-compose.yml`) — copy `.env.example` and fill in real values.
- **Local dev container**: `.env.dev` (git-ignored, loaded via `env_file` in
  `docker-compose.dev.yml`) — copy `.env.dev.example` (sqlite, debug on).
- **Production**: `.env.prod` (git-ignored, loaded via `env_file` in `docker-compose.prod.yml`) —
  copy `.env.prod.example` and fill in real values, see [deploy-checklist.md](deploy-checklist.md).

Each compose file sets its own Compose project name (`saldovibe-standalone`, `saldovibe-dev`;
`docker-compose.prod.yml` keeps the implicit directory-derived name) so their volumes and networks
never collide if multiple stacks run on the same host at once.
