from django.core.management.base import BaseCommand

from auditlog.models import AuditLogEntry
from auditlog.services import calculate_audit_entry_hash


class Command(BaseCommand):
    help = (
        "Recalculate and reseal audit hash chains for historical entries: the frozen "
        "global legacy chain (hash_version=1) and every per-company chain "
        "(hash_version>=2). --company-id reseals only that company's chain."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, help="Optional company id filter.")
        parser.add_argument("--start-id", type=int, help="Optional first AuditLogEntry id to process.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist updated prev_hash/entry_hash values. Default is dry-run.",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        start_id = options.get("start_id")
        apply_updates = options.get("apply", False)

        if company_id:
            # chain_key rather than the company FK: the FK is SET_NULL on company
            # delete, chain_key is the frozen chain identity.
            chains = [AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=str(company_id))]
        else:
            chains = [AuditLogEntry.objects.filter(hash_version=1)]
            chain_keys = (
                AuditLogEntry.objects.filter(hash_version__gte=2)
                .order_by()
                .values_list("chain_key", flat=True)
                .distinct()
            )
            chains += [
                AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=chain_key) for chain_key in chain_keys
            ]

        total = 0
        changed = 0

        for chain in chains:
            queryset = chain.order_by("id")
            prev_hash = ""

            # For partial reseal, prev hash must be inherited from the same chain's
            # entry before start id.
            if start_id:
                previous_entry = chain.filter(id__lt=start_id).order_by("-id").first()
                if previous_entry is not None:
                    prev_hash = previous_entry.entry_hash or ""
                queryset = queryset.filter(id__gte=start_id)

            for entry in queryset:
                total += 1
                expected_hash = calculate_audit_entry_hash(entry, prev_hash)
                needs_update = entry.prev_hash != prev_hash or entry.entry_hash != expected_hash
                if needs_update:
                    changed += 1
                    if apply_updates:
                        entry.prev_hash = prev_hash
                        entry.entry_hash = expected_hash
                        entry.save(update_fields=["prev_hash", "entry_hash"])
                prev_hash = expected_hash

        mode = "APPLY" if apply_updates else "DRY-RUN"
        self.stdout.write(self.style.WARNING(f"Mode: {mode}"))
        self.stdout.write(self.style.SUCCESS(f"Processed entries: {total}"))
        self.stdout.write(self.style.SUCCESS(f"Entries needing reseal: {changed}"))

        if not apply_updates:
            self.stdout.write(
                self.style.WARNING("No changes written. Re-run with --apply to persist recalculated hash chain.")
            )
