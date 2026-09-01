from django.conf import settings
from django.core.management.base import BaseCommand

from auditlog.models import AuditChainAnchor, AuditLogEntry
from auditlog.timestamping import TimestampRequestError, get_asserted_time, request_timestamp


class Command(BaseCommand):
    help = (
        "Send every audit chain's current tip hash (the frozen legacy chain plus each "
        "per-company chain) to an external RFC 3161 timestamp authority and store the "
        "signed responses, so tampering can be proven even after a reseal_audit_chain "
        "--apply. Safe to run repeatedly - skips tips that are already anchored."
    )

    def handle(self, *args, **options):
        tips = []
        legacy_tip = AuditLogEntry.objects.filter(hash_version=1).order_by("-id").first()
        if legacy_tip is not None:
            tips.append(legacy_tip)

        chain_keys = (
            AuditLogEntry.objects.filter(hash_version__gte=2).order_by().values_list("chain_key", flat=True).distinct()
        )
        for chain_key in sorted(chain_keys):
            tips.append(AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=chain_key).order_by("-id").first())

        tips = [tip for tip in tips if tip is not None and tip.entry_hash]
        if not tips:
            self.stdout.write(self.style.WARNING("Ingen auditlogg att förankra ännu."))
            return

        anchored = 0
        for tip in tips:
            if AuditChainAnchor.objects.filter(anchored_entry_hash=tip.entry_hash).exists():
                continue

            message = tip.entry_hash.encode("ascii")
            try:
                token = request_timestamp(message)
            except TimestampRequestError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                raise SystemExit(1)

            asserted_time = get_asserted_time(token)

            AuditChainAnchor.objects.create(
                anchored_entry=tip,
                anchored_entry_hash=tip.entry_hash,
                tsa_url=settings.AUDIT_CHAIN_TSA_URL,
                timestamp_token=token,
                tsa_asserted_time=asserted_time,
            )
            anchored += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Förankrade entry {tip.id} (hash {tip.entry_hash[:12]}...) hos {settings.AUDIT_CHAIN_TSA_URL} "
                    f"vid {asserted_time.isoformat()}."
                )
            )

        if anchored == 0:
            self.stdout.write(self.style.SUCCESS("Alla kedjespetsar är redan förankrade - inget nytt att göra."))
