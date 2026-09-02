# Deploy checklist (production)

Step-by-step for taking a new release of `main` live on the `docker-compose.yml` stack
(`web` + PostgreSQL `db` + `nginx`).

## First-time setup

1. Copy the env template and fill in real values:
   ```bash
   cp .env.example .env
   ```
   Only `docker-compose.yml` and `.env` are needed on the server — no checkout. Fetch them
   from `main` (matches the `latest` image; substitute a `vX.Y.Z` tag to pin a release):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/montegrotto/SaldoVibe/main/docker-compose.yml
   curl -fsSL -o .env https://raw.githubusercontent.com/montegrotto/SaldoVibe/main/.env.example
   ```
   Uncomment the whole "Produktion" section in the file. At minimum change
   `DJANGO_SECRET_KEY` and `DATABASE_PASSWORD` / `POSTGRES_PASSWORD` from `change-me`, and set
   `SALDOVIBE_PUBLIC_URL` to the real public URL (this drives `ALLOWED_HOSTS` and
   `CSRF_TRUSTED_ORIGINS` — see [environment-variables.md](environment-variables.md)).
2. **TLS is not terminated by the bundled `nginx` service** — the nginx config inlined in `docker-compose.yml` only listens on
   port 80. Put a TLS-terminating reverse proxy or load balancer in front of it (or add a TLS
   server block) before exposing the stack publicly.
3. Bring the stack up, either with the published image from Docker Hub or a local build:
   ```bash
   docker compose pull && docker compose up -d
   docker compose up --build -d   # build from this checkout instead
   ```
   Pin a release with `SALDOVIBE_VERSION=1.2.3` in the root `.env` (see `.env.example`).
   The `web` container runs `manage.py migrate --noinput` automatically on start (see
   [upgrades-migrations.md](upgrades-migrations.md)), so the schema is created on first boot.
4. Create an admin/first user through the app's own registration flow
   ([user guide, chapter 1](../user-guide/01-komma-igang.md)) — there is no separate Django
   superuser bootstrap step required for normal use.

## Routine release (code already merged to `main`)

1. **Back up first.** Take a PostgreSQL dump before deploying — see
   [backup-restore.md](backup-restore.md). A bad migration is much cheaper to recover from with a
   fresh dump than without one.
2. On the server, pull the new code:
   ```bash
   git pull origin main
   ```
3. Pull (or rebuild) and restart the `web` image — `migrate` runs at container start:
   ```bash
   docker compose pull web && docker compose up -d web
   docker compose up --build -d web   # local build instead
   ```
4. Watch the `web` logs during startup for migration errors or crash loops:
   ```bash
   docker compose logs -f web
   ```
5. Smoke-test the release:
   - Log in.
   - Open the dashboard for an existing company.
   - Create (or view) a verifikation to confirm DB writes work.
   - Check `/static/` assets load (no missing CSS/JS — a sign `collectstatic` didn't run).
6. If something is badly wrong, roll back to the previous image/tag and restore the pre-deploy
   backup if the migration already ran destructively (see
   [upgrades-migrations.md](upgrades-migrations.md) for rollback caveats).

## Zero-downtime note

This stack does **not** currently support rolling/zero-downtime deploys — `docker compose up
--build -d web` recreates the single `web` container, causing a brief outage while it restarts.
For a single-tenant/small-scale accounting app this is usually acceptable; if it stops being
acceptable, that's a signal to introduce multiple `web` replicas behind `nginx` with a health
check before cutting traffic over.

## After deploy

- Confirm scheduled jobs still run: the ofelia backup jobs and the `docs/compliance/` restore
  dry-run (`docker compose logs scheduler`, see
  [backup-restore.md](backup-restore.md) and `docs/compliance/restore-runbook.md`), plus the
  host's off-host backup sync, especially if anything in `.env` changed.
- Update `docs/compliance/quarterly-review-checklist.md` tracking if this release touched
  anything on that checklist (period locking, exports, audit logging).
