from django.conf import settings
from django.db import models


class AuditLogEntry(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Skapad"
        UPDATE = "update", "Uppdaterad"
        DELETE = "delete", "Raderad"

    occurred_at = models.DateTimeField("Tidpunkt", auto_now_add=True)
    action = models.CharField("Händelse", max_length=12, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_log_entries",
        verbose_name="Användare",
    )
    actor_display = models.CharField("Användare", max_length=255, blank=True)
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_log_entries",
        verbose_name="Företag",
    )
    company_name = models.CharField("Företagsnamn", max_length=200, blank=True)
    # Which hash chain the entry belongs to: the company's pk as a string, or "" for
    # entries without a company. Frozen at write time (unlike the company FK, which is
    # SET_NULL on company delete) so a chain stays verifiable per company forever.
    # Entries with hash_version=1 predate per-company chains and form one frozen
    # global legacy chain; hash_version>=2 entries chain per chain_key.
    chain_key = models.CharField("Kedjenyckel", max_length=32, blank=True, default="")
    model_label = models.CharField("Modell", max_length=120)
    model_name = models.CharField("Objekttyp", max_length=120)
    object_pk = models.CharField("Objekt-ID", max_length=64)
    object_repr = models.CharField("Objekt", max_length=255)
    summary = models.CharField("Sammanfattning", max_length=255)
    changes = models.JSONField("Ändringar", default=dict, blank=True)
    metadata = models.JSONField("Metadata", default=dict, blank=True)
    hash_version = models.PositiveSmallIntegerField("Hashversion", default=1)
    prev_hash = models.CharField("Föregående hash", max_length=64, blank=True)
    entry_hash = models.CharField("Entry hash", max_length=64, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "Logghändelse"
        verbose_name_plural = "Logghändelser"
        indexes = [
            models.Index(fields=["company", "occurred_at"]),
            models.Index(fields=["chain_key", "id"]),
            models.Index(fields=["model_label", "occurred_at"]),
            models.Index(fields=["action", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()}: {self.model_name} {self.object_repr}"


class AuditChainTip(models.Model):
    """Pure lock row, one per hash chain (see AuditLogEntry.chain_key). create_audit_log
    locks it FOR UPDATE before reading the chain's last entry, so two concurrent writers
    can't both chain to the same prev_hash — locking the last entry row itself doesn't
    work under READ COMMITTED, where FOR UPDATE never sees a row a concurrent transaction
    committed after the sort. Holds no state; a lost row is recreated on the next write.
    """

    chain_key = models.CharField("Kedjenyckel", max_length=32, unique=True)

    class Meta:
        verbose_name = "Auditkedje-spets"
        verbose_name_plural = "Auditkedje-spetsar"

    def __str__(self):
        return f"Kedjespets {self.chain_key or '(utan företag)'}"


class AuditChainAnchor(models.Model):
    """A record that the audit chain's tip hash, at a point in time, was sent
    to an external RFC 3161 timestamp authority and cryptographically signed.
    See auditlog/timestamping.py for why this exists: it survives a
    `reseal_audit_chain --apply` in a way the internal hash chain alone can't,
    since resealing after tampering can't retroactively change what an
    independent third party already attested to.
    """

    anchored_entry = models.ForeignKey(
        AuditLogEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chain_anchors",
        verbose_name="Förankrad logghändelse",
    )
    anchored_entry_hash = models.CharField(
        "Förankrad hash",
        max_length=64,
        help_text="entry_hash-värdet vid tidpunkten för förankringen, bevarat även om posten senare ändras.",
    )
    tsa_url = models.CharField("TSA-URL", max_length=255)
    timestamp_token = models.BinaryField("Tidsstämpeltoken (RFC 3161)")
    tsa_asserted_time = models.DateTimeField("TSA:s tidsstämpel")
    created_at = models.DateTimeField("Skapad", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Auditkedje-förankring"
        verbose_name_plural = "Auditkedje-förankringar"

    def __str__(self):
        return f"Förankring {self.anchored_entry_hash[:12]}... ({self.created_at:%Y-%m-%d})"
