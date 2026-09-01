from django.conf import settings
from django.db import models


class VatCloseSnapshot(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="vat_close_snapshots",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        "bookkeeping.AccountingYear",
        on_delete=models.CASCADE,
        related_name="vat_close_snapshots",
        verbose_name="Räkenskapsår",
    )
    period_start = models.DateField("Periodstart")
    period_end = models.DateField("Periodslut")
    closed_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vat_close_snapshots",
        verbose_name="Stängningsverifikation",
    )
    vat_boxes = models.JSONField("Momsrutor", default=dict)
    source_transaction_ids = models.JSONField("Källverifikationer", default=list)
    source_fingerprint = models.CharField("Källfingeravtryck", max_length=64)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vat_close_snapshots",
        verbose_name="Stängd av",
    )
    closed_at = models.DateTimeField("Stängd vid", auto_now_add=True)

    class Meta:
        ordering = ["-period_start", "-id"]
        verbose_name = "Momsstängningsbevis"
        verbose_name_plural = "Momsstängningsbevis"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "accounting_year", "period_start", "period_end"],
                name="uniq_vat_close_snapshot_per_period",
            ),
        ]

    def __str__(self):
        return f"{self.company.name}: {self.period_start} - {self.period_end}"
