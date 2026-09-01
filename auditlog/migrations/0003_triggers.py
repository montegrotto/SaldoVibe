from django.db import migrations

# Audit log entries are append-only at the database level, not just in application code.
#
# The internal hash chain is deliberately only tamper-evident, not tamper-proof - the external
# RFC 3161 anchor (AuditChainAnchor) is what actually defeats a tamper-then-reseal attack (see
# auditlog/timestamping.py). So reseal_audit_chain --apply rewriting prev_hash/entry_hash across
# the chain, and create_audit_log's own post-insert hash stamp (auditlog/services.py), both need
# to keep working. actor/company are also on_delete=SET_NULL, so deleting a user or company
# issues an UPDATE ... SET actor_id/company_id = NULL on every entry that referenced them -
# actor_display/company_name are the denormalized snapshot of what they were, and stay frozen
# like every other content field. Only those four columns are left updatable; everything else,
# and any delete, is blocked outright.
#
# The triggers live only here (no model-level counterpart). Everything is written so that
# re-running it is harmless (DROP IF EXISTS / CREATE OR REPLACE), which matters on sqlite: a
# later migration that rebuilds auditlog_auditlogentry (AddField, AddConstraint ...) silently
# drops the table's triggers, and the fix is a new migration that re-runs add_triggers. The
# tests in auditlog/tests.py exercise the triggers and fail if they are missing.

_FROZEN_FIELDS = [
    "occurred_at",
    "action",
    "actor_display",
    "company_name",
    "model_label",
    "model_name",
    "object_pk",
    "object_repr",
    "summary",
    "changes",
    "metadata",
    "hash_version",
    "chain_key",
]

_UPDATE_MESSAGE = "Auditloggposter kan inte ändras i efterhand, förutom hash-kedjans egna fält."
_DELETE_MESSAGE = "Auditloggposter kan inte raderas."

_SQLITE_UPDATE_CONDITIONS = "\n            AND ".join(f"NEW.{f} IS OLD.{f}" for f in _FROZEN_FIELDS)
_SQLITE_TRIGGERS = {
    "trg_auditlog_auditlogentry_no_update": f"""
        CREATE TRIGGER trg_auditlog_auditlogentry_no_update
        BEFORE UPDATE ON auditlog_auditlogentry
        WHEN NOT (
            {_SQLITE_UPDATE_CONDITIONS}
        )
        BEGIN
            SELECT RAISE(ABORT, '{_UPDATE_MESSAGE}');
        END
    """,
    "trg_auditlog_auditlogentry_no_delete": f"""
        CREATE TRIGGER trg_auditlog_auditlogentry_no_delete
        BEFORE DELETE ON auditlog_auditlogentry
        BEGIN
            SELECT RAISE(ABORT, '{_DELETE_MESSAGE}');
        END
    """,
}

_POSTGRES_UPDATE_CONDITIONS = "\n            AND ".join(f"NEW.{f} IS NOT DISTINCT FROM OLD.{f}" for f in _FROZEN_FIELDS)
_POSTGRES_CREATE = f"""
    CREATE OR REPLACE FUNCTION auditlog_auditlogentry_block_update() RETURNS trigger AS $$
    BEGIN
        IF NOT (
            {_POSTGRES_UPDATE_CONDITIONS}
        ) THEN
            RAISE EXCEPTION '{_UPDATE_MESSAGE}' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_auditlog_auditlogentry_no_update ON auditlog_auditlogentry;
    CREATE TRIGGER trg_auditlog_auditlogentry_no_update
    BEFORE UPDATE ON auditlog_auditlogentry
    FOR EACH ROW
    EXECUTE FUNCTION auditlog_auditlogentry_block_update();

    CREATE OR REPLACE FUNCTION auditlog_auditlogentry_block_delete() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION '{_DELETE_MESSAGE}' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_auditlog_auditlogentry_no_delete ON auditlog_auditlogentry;
    CREATE TRIGGER trg_auditlog_auditlogentry_no_delete
    BEFORE DELETE ON auditlog_auditlogentry
    FOR EACH ROW
    EXECUTE FUNCTION auditlog_auditlogentry_block_delete();
"""

_POSTGRES_DROP = """
    DROP TRIGGER IF EXISTS trg_auditlog_auditlogentry_no_delete ON auditlog_auditlogentry;
    DROP FUNCTION IF EXISTS auditlog_auditlogentry_block_delete();
    DROP TRIGGER IF EXISTS trg_auditlog_auditlogentry_no_update ON auditlog_auditlogentry;
    DROP FUNCTION IF EXISTS auditlog_auditlogentry_block_update();
"""


def add_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "sqlite":
        for name, sql in _SQLITE_TRIGGERS.items():
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")
            schema_editor.execute(sql)
    elif vendor == "postgresql":
        schema_editor.execute(_POSTGRES_CREATE)


def drop_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "sqlite":
        for name in _SQLITE_TRIGGERS:
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")
    elif vendor == "postgresql":
        schema_editor.execute(_POSTGRES_DROP)


class Migration(migrations.Migration):
    dependencies = [
        ("auditlog", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(add_triggers, drop_triggers),
    ]
