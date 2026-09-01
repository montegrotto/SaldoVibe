# Upgrades & migrations

## How migrations run today

`scripts/docker-entrypoint.sh` runs on every `web` container start:

```sh
if [ "${SALDOVIBE_RUN_MIGRATIONS:-1}" = "1" ]; then
    /opt/venv/bin/python /app/manage.py migrate --noinput
fi
```

This means **every restart of the `web` container applies any pending migrations automatically**,
including during a routine deploy (see [deploy-checklist.md](deploy-checklist.md)). There is no
separate "run migrations" step you need to remember for normal releases.

Set `SALDOVIBE_RUN_MIGRATIONS=0` in `.env.prod` if you ever want to decouple migration application
from container start (e.g. running `migrate` manually in a maintenance window before starting the
new `web` image).

## Running migrations manually

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

To preview what a deploy would change without applying it:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate --plan
```

To check that no model changes are missing a migration file (useful in CI or before merging):

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py makemigrations --check --dry-run
```

## Before a migration that touches accounting tables

Migrations affecting `bookkeeping`, `banking`, `payroll`, `vat`, `invoicing`,
`supplier_invoices`, `fixed_assets`, or `auditlog` models carry more risk than a typical Django app
because of the compliance constraints already enforced in the app layer (balanced journal entries,
voucher numbering, period locks, audit hash chain — see `docs/compliance/` and
`docs/system-replication-spec.md` section 5). Before deploying such a migration:

1. Take a fresh PostgreSQL backup (see [backup-restore.md](backup-restore.md)).
2. Read the migration file, not just the model diff — Django's auto-generated migrations can pick
   surprising defaults for new non-nullable fields on tables that already have rows.
3. If the migration changes anything in the audit-logged models, run
   `python manage.py verify_audit_chain` **after** deploying to confirm the hash chain wasn't
   disturbed (e.g. by a data migration touching audited fields directly instead of through the
   normal model/signal path).

## Rolling back

Django migrations can be reversed if the migration defines a working `reverse` operation (most
auto-generated schema migrations do; hand-written data migrations may not):

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate <app_label> <previous_migration_name>
```

Caveats:

- Rolling back the **schema** does not undo **data** changes a migration may have made (e.g. a
  data migration that backfilled a new field). Check the migration file for a `RunPython` step
  before assuming a rollback is clean.
- If in doubt, prefer restoring the pre-deploy PostgreSQL backup over trying to reverse-migrate a
  production database — see [backup-restore.md](backup-restore.md).
- Roll back the `web` container image alongside the schema — running new code against an old
  schema (or vice versa) is the more common source of breakage than the migration itself.

## Related management commands

| Command | Purpose |
|---|---|
| `manage.py migrate` | Apply/roll back schema migrations. |
| `manage.py makemigrations --check --dry-run` | Verify no model changes are missing migrations. |
| `manage.py verify_audit_chain` | Confirm the audit hash chain is intact after a deploy/migration. |
| `manage.py reseal_audit_chain` | Recompute `prev_hash`/`entry_hash` (dry-run by default, `--apply` to persist) — only for deliberate, understood repairs, not routine use. |
| `manage.py load_bas_accounts` | (Re)load the BAS chart-of-accounts fixture used when seeding new companies. |
| `manage.py populate_sru_codes` | Backfill SRU codes onto existing accounts. |
