from django.core.management.base import BaseCommand

from auditlog.models import AuditChainAnchor
from auditlog.timestamping import TimestampVerificationError, verify_timestamp_token


class Command(BaseCommand):
    help = (
        "Verify every stored external timestamp anchor: that the RFC 3161 token is "
        "cryptographically valid, and that the anchored entry's hash in the database "
        "still matches what was attested at anchor time. A mismatch here proves "
        "tampering even if `verify_audit_chain` reports the chain as internally "
        "consistent (e.g. after a reseal_audit_chain --apply)."
    )

    def handle(self, *args, **options):
        anchors = list(AuditChainAnchor.objects.order_by("id"))
        if not anchors:
            self.stdout.write(self.style.WARNING("Inga förankringar att verifiera ännu."))
            return

        problems = []

        for anchor in anchors:
            message = anchor.anchored_entry_hash.encode("ascii")
            try:
                verify_timestamp_token(bytes(anchor.timestamp_token), message)
            except TimestampVerificationError as exc:
                problems.append(f"Förankring {anchor.id}: tidsstämpeln kunde inte kryptografiskt verifieras ({exc}).")
                continue

            if anchor.anchored_entry_id is None:
                problems.append(f"Förankring {anchor.id}: den förankrade logghändelsen finns inte längre i databasen.")
                continue

            current_hash = anchor.anchored_entry.entry_hash if anchor.anchored_entry is not None else None
            if current_hash != anchor.anchored_entry_hash:
                problems.append(
                    f"Förankring {anchor.id} (entry {anchor.anchored_entry_id}): "
                    f"tidsstämplad hash {anchor.anchored_entry_hash[:12]}... matchar inte "
                    f"nuvarande hash {(current_hash or '(saknas)')[:12]}... - kedjan har manipulerats "
                    "sedan förankringen, oavsett vad en senare reseal_audit_chain visar."
                )

        if problems:
            self.stdout.write(self.style.ERROR(f"{len(problems)} förankring(ar) misslyckades:"))
            for problem in problems:
                self.stdout.write(self.style.ERROR(f"- {problem}"))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"Alla {len(anchors)} förankringar verifierade utan avvikelser."))
