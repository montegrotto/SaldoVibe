import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TABLES_TO_VERIFY = [
    "bookkeeping_company",
    "bookkeeping_accountingyear",
    "bookkeeping_transaction",
    "bookkeeping_journalentry",
    "auditlog_auditlogentry",
    "bookkeeping_transactionattachment",
]


class Command(BaseCommand):
    help = (
        "Restore the accounting database into an isolated copy/database and verify record "
        "counts, producing an evidence report. Supports SQLite (local/dev) and PostgreSQL "
        "(production) via the active DATABASES['default'] engine."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default="docs/compliance/evidence/restore-tests",
            help="Directory where the restore evidence report will be written.",
        )

    def handle(self, *args, **options):
        db_settings = settings.DATABASES["default"]
        engine = db_settings["ENGINE"]

        output_dir = Path(options["output_dir"]).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        if engine == "django.db.backends.sqlite3":
            report = self._run_sqlite_dry_run(db_settings, output_dir, timestamp)
        elif engine in ("django.db.backends.postgresql", "django.db.backends.postgresql_psycopg2"):
            report = self._run_postgresql_dry_run(db_settings, output_dir, timestamp)
        else:
            raise CommandError(f"Restore dry-run stöds inte för databasmotorn: {engine}")

        report_file = output_dir / f"restore-report-{timestamp}.json"
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS("Restore dry-run completed."))
        self.stdout.write(self.style.SUCCESS(f"Report: {report_file}"))

    def _sha256(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _run_sqlite_dry_run(self, db_settings, output_dir, timestamp):
        db_path = Path(db_settings["NAME"]).resolve()
        if not db_path.exists():
            raise CommandError(f"Databasfil saknas: {db_path}")

        restored_copy = output_dir / f"restore-copy-{timestamp}.sqlite3"
        shutil.copy2(db_path, restored_copy)

        table_counts = {}
        with sqlite3.connect(restored_copy) as conn:
            cursor = conn.cursor()
            for table in TABLES_TO_VERIFY:
                cursor.execute(f"SELECT COUNT(1) FROM {table}")
                table_counts[table] = int(cursor.fetchone()[0])

        return {
            "schema": "restore-dry-run-v1",
            "engine": "sqlite3",
            "generated_at_utc": timestamp,
            "source_database": str(db_path),
            "restored_copy": str(restored_copy),
            "source_sha256": self._sha256(db_path),
            "restored_sha256": self._sha256(restored_copy),
            "table_counts": table_counts,
            "status": "ok",
        }

    def _run_postgresql_dry_run(self, db_settings, output_dir, timestamp):
        import psycopg

        source_db = db_settings["NAME"]
        user = db_settings.get("USER") or "postgres"
        password = db_settings.get("PASSWORD") or ""
        host = db_settings.get("HOST") or "localhost"
        port = str(db_settings.get("PORT") or "5432")
        restore_db = f"{source_db}_restore_dryrun_{timestamp.lower()}"
        dump_file = output_dir / f"restore-dump-{timestamp}.dump"

        run_env = dict(os.environ)
        if password:
            run_env["PGPASSWORD"] = password

        def run(cmd):
            for binary in ("pg_dump", "pg_restore", "createdb", "dropdb"):
                if cmd[0] == binary and shutil.which(binary) is None:
                    raise CommandError(f"Kommandot '{binary}' hittades inte. Installera postgresql-client i körmiljön.")
            result = subprocess.run(cmd, env=run_env, capture_output=True, text=True)
            if result.returncode != 0:
                raise CommandError(f"Kommando misslyckades ({' '.join(cmd)}): {result.stderr.strip()}")
            return result

        connection_args = ["-h", host, "-p", port, "-U", user]

        # 1) Dump the live database (custom format, restorable in isolation with pg_restore).
        run(["pg_dump", *connection_args, "-F", "c", "-f", str(dump_file), source_db])

        # 2) Restore into a throwaway database so the dry-run never touches the live database.
        run(["createdb", *connection_args, restore_db])
        try:
            run(["pg_restore", *connection_args, "-d", restore_db, str(dump_file)])

            table_counts = {}
            with psycopg.connect(host=host, port=port, user=user, password=password, dbname=restore_db) as conn:
                with conn.cursor() as cur:
                    for table in TABLES_TO_VERIFY:
                        cur.execute(f"SELECT COUNT(1) FROM {table}")
                        table_counts[table] = int(cur.fetchone()[0])
        finally:
            # 3) Always drop the throwaway database, on success or failure, to avoid accumulating
            # full copies of production data outside the primary database's access controls.
            run(["dropdb", "--if-exists", *connection_args, restore_db])

        return {
            "schema": "restore-dry-run-v1",
            "engine": "postgresql",
            "generated_at_utc": timestamp,
            "source_database": source_db,
            "restore_database": restore_db,
            "dump_file": str(dump_file),
            "dump_sha256": self._sha256(dump_file),
            "table_counts": table_counts,
            "status": "ok",
        }
