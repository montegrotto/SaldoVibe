from django.db import migrations

# Posted vouchers are immutable at the database level, not just in application code.
#
# Transaction/JournalEntry rows are append-only in every application flow except one: SIE full
# re-import (bookkeeping/views/imports.py) bulk-deletes existing Transaction rows for a year, but
# only the ones _locked_transactions_q excludes as locked. So the invariant enforced here is
# "never touch a locked period", not "never write again" - a blanket block would break that
# re-import. Both correction_of and created_by are on_delete=SET_NULL, so deleting a
# corrected-of Transaction, or a user who has ever posted one, issues an
# UPDATE ... SET correction_of_id/created_by_id = NULL on the affected row(s); those two columns
# are left updatable so both cascades still work. Journal entries can never be updated, and can
# neither be deleted from nor inserted into a voucher whose date falls in a locked period (an
# insert would also make the voucher unbalanced). All application flows create the entries
# together with the voucher while the period is provably open (Transaction.save), so the insert
# trigger is purely a safety net.
#
# The triggers live only here (no model-level counterpart). Everything is written so that
# re-running it is harmless (DROP IF EXISTS / CREATE OR REPLACE), which matters on sqlite: a
# later migration that rebuilds bookkeeping_transaction or bookkeeping_journalentry (AddField,
# AddConstraint ...) silently drops the table's triggers, and the fix is a new migration that
# re-runs add_triggers. bookkeeping/tests/test_ledger_immutability_triggers.py exercises the
# triggers and fails if they are missing.

_LOCKED_PERIOD_FOR_ENTRY_SQLITE = """
        SELECT 1
        FROM bookkeeping_transaction t
        JOIN bookkeeping_accountingyear ay ON ay.id = t.accounting_year_id
        JOIN bookkeeping_periodlock pl ON pl.company_id = ay.company_id
        WHERE t.id = {row}.transaction_id
          AND pl.is_locked = 1
          AND pl.period_start <= t."date"
          AND pl.period_end >= t."date"
"""

_SQLITE_TRIGGERS = {
    "trg_bookkeeping_transaction_no_update": """
        CREATE TRIGGER trg_bookkeeping_transaction_no_update
        BEFORE UPDATE ON bookkeeping_transaction
        WHEN NOT (
            NEW.accounting_year_id IS OLD.accounting_year_id
            AND NEW."date" IS OLD."date"
            AND NEW.description IS OLD.description
            AND NEW.reference IS OLD.reference
            AND NEW.source IS OLD.source
            AND NEW.voucher_series IS OLD.voucher_series
            AND NEW.voucher_number IS OLD.voucher_number
            AND NEW.created_at IS OLD.created_at
            AND NEW.updated_at IS OLD.updated_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'Bokförda verifikationer kan inte ändras. Skapa en korrigeringsverifikation istället.');
        END
    """,
    "trg_bookkeeping_transaction_no_delete_locked": """
        CREATE TRIGGER trg_bookkeeping_transaction_no_delete_locked
        BEFORE DELETE ON bookkeeping_transaction
        WHEN EXISTS (
            SELECT 1
            FROM bookkeeping_periodlock pl
            JOIN bookkeeping_accountingyear ay ON ay.id = OLD.accounting_year_id
            WHERE pl.company_id = ay.company_id
              AND pl.is_locked = 1
              AND pl.period_start <= OLD."date"
              AND pl.period_end >= OLD."date"
        )
        BEGIN
            SELECT RAISE(ABORT, 'Verifikationen ligger i en låst period och kan inte raderas.');
        END
    """,
    "trg_bookkeeping_journalentry_no_update": """
        CREATE TRIGGER trg_bookkeeping_journalentry_no_update
        BEFORE UPDATE ON bookkeeping_journalentry
        BEGIN
            SELECT RAISE(ABORT, 'Konteringsrader kan inte ändras efter bokföring.');
        END
    """,
    "trg_bookkeeping_journalentry_no_delete_locked": f"""
        CREATE TRIGGER trg_bookkeeping_journalentry_no_delete_locked
        BEFORE DELETE ON bookkeeping_journalentry
        WHEN EXISTS ({_LOCKED_PERIOD_FOR_ENTRY_SQLITE.format(row="OLD")}
        )
        BEGIN
            SELECT RAISE(ABORT, 'Konteringsraden ligger i en låst period och kan inte raderas.');
        END
    """,
    "trg_bookkeeping_journalentry_no_insert_locked": f"""
        CREATE TRIGGER trg_bookkeeping_journalentry_no_insert_locked
        BEFORE INSERT ON bookkeeping_journalentry
        WHEN EXISTS ({_LOCKED_PERIOD_FOR_ENTRY_SQLITE.format(row="NEW")}
        )
        BEGIN
            SELECT RAISE(ABORT, 'Verifikationen ligger i en låst period – konteringsrader kan inte läggas till.');
        END
    """,
}

_LOCKED_PERIOD_FOR_ENTRY_POSTGRES = """
            SELECT 1
            FROM bookkeeping_transaction t
            JOIN bookkeeping_accountingyear ay ON ay.id = t.accounting_year_id
            JOIN bookkeeping_periodlock pl ON pl.company_id = ay.company_id
            WHERE t.id = {row}.transaction_id
              AND pl.is_locked = true
              AND pl.period_start <= t.date
              AND pl.period_end >= t.date
"""

_POSTGRES_CREATE = f"""
    CREATE OR REPLACE FUNCTION bookkeeping_transaction_block_update() RETURNS trigger AS $$
    BEGIN
        IF NOT (
            NEW.accounting_year_id IS NOT DISTINCT FROM OLD.accounting_year_id
            AND NEW.date IS NOT DISTINCT FROM OLD.date
            AND NEW.description IS NOT DISTINCT FROM OLD.description
            AND NEW.reference IS NOT DISTINCT FROM OLD.reference
            AND NEW.source IS NOT DISTINCT FROM OLD.source
            AND NEW.voucher_series IS NOT DISTINCT FROM OLD.voucher_series
            AND NEW.voucher_number IS NOT DISTINCT FROM OLD.voucher_number
            AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
            AND NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at
        ) THEN
            RAISE EXCEPTION 'Bokförda verifikationer kan inte ändras. Skapa en korrigeringsverifikation istället.'
                USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_bookkeeping_transaction_no_update ON bookkeeping_transaction;
    CREATE TRIGGER trg_bookkeeping_transaction_no_update
    BEFORE UPDATE ON bookkeeping_transaction
    FOR EACH ROW
    EXECUTE FUNCTION bookkeeping_transaction_block_update();

    CREATE OR REPLACE FUNCTION bookkeeping_transaction_block_delete_locked() RETURNS trigger AS $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM bookkeeping_periodlock pl
            JOIN bookkeeping_accountingyear ay ON ay.id = OLD.accounting_year_id
            WHERE pl.company_id = ay.company_id
              AND pl.is_locked = true
              AND pl.period_start <= OLD.date
              AND pl.period_end >= OLD.date
        ) THEN
            RAISE EXCEPTION 'Verifikationen ligger i en låst period och kan inte raderas.'
                USING ERRCODE = '23000';
        END IF;
        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_bookkeeping_transaction_no_delete_locked ON bookkeeping_transaction;
    CREATE TRIGGER trg_bookkeeping_transaction_no_delete_locked
    BEFORE DELETE ON bookkeeping_transaction
    FOR EACH ROW
    EXECUTE FUNCTION bookkeeping_transaction_block_delete_locked();

    CREATE OR REPLACE FUNCTION bookkeeping_journalentry_block_update() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'Konteringsrader kan inte ändras efter bokföring.'
            USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_update ON bookkeeping_journalentry;
    CREATE TRIGGER trg_bookkeeping_journalentry_no_update
    BEFORE UPDATE ON bookkeeping_journalentry
    FOR EACH ROW
    EXECUTE FUNCTION bookkeeping_journalentry_block_update();

    CREATE OR REPLACE FUNCTION bookkeeping_journalentry_block_delete_locked() RETURNS trigger AS $$
    BEGIN
        IF EXISTS ({_LOCKED_PERIOD_FOR_ENTRY_POSTGRES.format(row="OLD")}
        ) THEN
            RAISE EXCEPTION 'Konteringsraden ligger i en låst period och kan inte raderas.'
                USING ERRCODE = '23000';
        END IF;
        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_delete_locked ON bookkeeping_journalentry;
    CREATE TRIGGER trg_bookkeeping_journalentry_no_delete_locked
    BEFORE DELETE ON bookkeeping_journalentry
    FOR EACH ROW
    EXECUTE FUNCTION bookkeeping_journalentry_block_delete_locked();

    CREATE OR REPLACE FUNCTION bookkeeping_journalentry_block_insert_locked() RETURNS trigger AS $$
    BEGIN
        IF EXISTS ({_LOCKED_PERIOD_FOR_ENTRY_POSTGRES.format(row="NEW")}
        ) THEN
            RAISE EXCEPTION 'Verifikationen ligger i en låst period – konteringsrader kan inte läggas till.'
                USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_insert_locked ON bookkeeping_journalentry;
    CREATE TRIGGER trg_bookkeeping_journalentry_no_insert_locked
    BEFORE INSERT ON bookkeeping_journalentry
    FOR EACH ROW
    EXECUTE FUNCTION bookkeeping_journalentry_block_insert_locked();
"""

_POSTGRES_DROP = """
    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_insert_locked ON bookkeeping_journalentry;
    DROP FUNCTION IF EXISTS bookkeeping_journalentry_block_insert_locked();
    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_delete_locked ON bookkeeping_journalentry;
    DROP FUNCTION IF EXISTS bookkeeping_journalentry_block_delete_locked();
    DROP TRIGGER IF EXISTS trg_bookkeeping_journalentry_no_update ON bookkeeping_journalentry;
    DROP FUNCTION IF EXISTS bookkeeping_journalentry_block_update();
    DROP TRIGGER IF EXISTS trg_bookkeeping_transaction_no_delete_locked ON bookkeeping_transaction;
    DROP FUNCTION IF EXISTS bookkeeping_transaction_block_delete_locked();
    DROP TRIGGER IF EXISTS trg_bookkeeping_transaction_no_update ON bookkeeping_transaction;
    DROP FUNCTION IF EXISTS bookkeeping_transaction_block_update();
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
        ("bookkeeping", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(add_triggers, drop_triggers),
    ]
