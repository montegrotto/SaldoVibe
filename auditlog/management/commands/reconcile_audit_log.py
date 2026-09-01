from django.apps import apps
from django.core.management.base import BaseCommand

from auditlog.models import AuditLogEntry
from auditlog.services import TRACKED_MODELS


class Command(BaseCommand):
    help = (
        "Reconcile the audit log against live rows: find objects the log recorded as created "
        "(and never as deleted) that are now missing from the database - the trace a raw "
        "SQL DELETE leaves, since it bypasses the ORM signals that would log the deletion. "
        "Objects created before auditing existed have no CREATE entry and can't be checked."
    )

    def handle(self, *args, **options):
        anomalies = []

        for label in TRACKED_MODELS:
            model = apps.get_model(label)

            created = set(
                AuditLogEntry.objects.filter(model_label=label, action=AuditLogEntry.Action.CREATE).values_list(
                    "object_pk", flat=True
                )
            )
            if not created:
                continue
            deleted = set(
                AuditLogEntry.objects.filter(model_label=label, action=AuditLogEntry.Action.DELETE).values_list(
                    "object_pk", flat=True
                )
            )
            expected_live = created - deleted
            if not expected_live:
                continue

            # _base_manager, not objects: reconciliation must see every row, past any
            # custom default manager that filters (e.g. a soft-delete manager).
            live = {str(pk) for pk in model._base_manager.values_list("pk", flat=True)}
            for pk in sorted(expected_live - live):
                anomalies.append((label, pk))

        if anomalies:
            self.stdout.write(self.style.ERROR("Auditloggen och databasen stämmer inte:"))
            for label, pk in anomalies:
                self.stdout.write(
                    self.style.ERROR(f"- {label} {pk}: skapad enligt loggen, aldrig raderad, saknas i databasen")
                )
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Auditloggen och databasen stämmer överens."))
