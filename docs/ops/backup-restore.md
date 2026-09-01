# Production backup & restore

This covers **infrastructure-level** backup/restore for the `docker-compose.prod.yml` stack
(PostgreSQL + media/static volumes). It is separate from
[`docs/compliance/restore-runbook.md`](../compliance/restore-runbook.md), which only exercises the
**sqlite** dev/desktop database as an audit-evidence dry-run (`compliance_restore_dry_run`
management command) — that command reads `settings.DATABASES['default']['NAME']` as a sqlite file
path and does not work against PostgreSQL at all.

## What needs to be backed up

| Data | Where it lives in the prod stack | Loss impact |
|---|---|---|
| PostgreSQL database | `saldovibe-postgres` volume (via the `db` service) | All accounting data, users, companies |
| Uploaded media (attachments, invoices, company logos) | `media-assets` volume, mounted at `/data/media` in `web` | Underlag/bilagor referenced by bokförda poster |
| Static assets | `static-assets` volume | Regeneratable via `collectstatic` — not critical to back up |
| `.env.prod` | Git-ignored file on the host | Without it you cannot recreate the stack's secrets/config |

## Backing up PostgreSQL

Run a logical dump from the host, through the running `db` container, on a schedule (cron or
similar):

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U saldovibe -d saldovibe --format=custom \
  > backups/saldovibe-$(date +%Y%m%dT%H%M%S).dump
```

- `--format=custom` produces a compressed dump that works with `pg_restore` and supports
  selective/parallel restore.
- Keep the credentials in sync with `.env.prod` (`DATABASE_USER` / `POSTGRES_USER` etc, see
  [environment-variables.md](environment-variables.md)).
- Store dumps off the host (object storage, another server) — a backup that only lives on the
  same disk as the database is not a backup.

## Backing up media

```bash
docker run --rm \
  -v saldovibe_media-assets:/media:ro \
  -v "$(pwd)/backups":/backup \
  alpine tar czf /backup/saldovibe-media-$(date +%Y%m%dT%H%M%S).tar.gz -C /media .
```

Adjust the volume name if Compose has prefixed it differently (`docker volume ls` to confirm — the
prefix is normally the Compose project/directory name).

## Restoring PostgreSQL

1. Stop the `web` service so nothing writes during restore (`db` can stay up):
   ```bash
   docker compose -f docker-compose.prod.yml stop web
   ```
2. Restore into a fresh or emptied database:
   ```bash
   docker compose -f docker-compose.prod.yml exec -T db \
     pg_restore -U saldovibe -d saldovibe --clean --if-exists \
     < backups/saldovibe-<timestamp>.dump
   ```
3. Start `web` again — the entrypoint runs `migrate --noinput` automatically (see
   [upgrades-migrations.md](upgrades-migrations.md)), which is a no-op if the restored dump is
   already at the current migration state:
   ```bash
   docker compose -f docker-compose.prod.yml start web
   ```

## Restoring media

```bash
docker run --rm \
  -v saldovibe_media-assets:/media \
  -v "$(pwd)/backups":/backup \
  alpine sh -c "rm -rf /media/* && tar xzf /backup/saldovibe-media-<timestamp>.tar.gz -C /media"
```

## Verifying a restore actually worked

After restoring into a **staging** copy of the stack (never verify destructively against
production):

- Log in and confirm a known company/transaction from before the backup is present.
- Open a known attachment/invoice PDF and confirm the file itself opens (not just the DB row).
- Run `python manage.py verify_audit_chain` against the restored database to confirm the audit
  hash chain is intact (see [logging-monitoring.md](logging-monitoring.md) and
  `docs/compliance/`).

## Recommended cadence

- **Nightly**: PostgreSQL dump.
- **Weekly**: media backup (changes less often, but grows and must not be forgotten).
- **Quarterly**: full restore-to-staging test, not just "the backup file exists" — an untested
  backup is not a backup. Pair this with the existing
  `docs/compliance/quarterly-review-checklist.md`.
