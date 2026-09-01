from django.core.management.base import BaseCommand

from auditlog.models import AuditLogEntry
from auditlog.services import calculate_audit_entry_hash


class Command(BaseCommand):
    help = (
        "Verify the audit log hash chain integrity: the frozen global legacy chain "
        "(hash_version=1) plus every per-company chain (hash_version>=2, grouped by chain_key)."
    )

    def handle(self, *args, **options):
        broken_entries = []

        self._verify_chain(AuditLogEntry.objects.filter(hash_version=1), broken_entries)

        chain_keys = (
            AuditLogEntry.objects.filter(hash_version__gte=2).order_by().values_list("chain_key", flat=True).distinct()
        )
        for chain_key in chain_keys:
            self._verify_chain(
                AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=chain_key),
                broken_entries,
            )

        if broken_entries:
            self.stdout.write(self.style.ERROR("Auditkedjan innehåller fel:"))
            for entry_id, field_name in broken_entries:
                self.stdout.write(self.style.ERROR(f"- Entry {entry_id}: fel i {field_name}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("Auditkedjan är giltig."))

    def _verify_chain(self, queryset, broken_entries):
        expected_prev_hash = ""

        for entry in queryset.order_by("id"):
            if entry.prev_hash != expected_prev_hash:
                broken_entries.append((entry.id, "prev_hash"))
                expected_prev_hash = entry.entry_hash
                continue

            calculated_hash = calculate_audit_entry_hash(entry, entry.prev_hash)
            if entry.entry_hash != calculated_hash:
                broken_entries.append((entry.id, "entry_hash"))

            expected_prev_hash = entry.entry_hash
