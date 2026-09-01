from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class BankAccountType(models.TextChoices):
    BANK = "bank", "Bankkonto"
    TAX = "tax", "Skattekonto"
    SAVINGS = "savings", "Sparkonto"
    CREDIT_CARD = "credit_card", "Kreditkort"


class BankProfile(models.TextChoices):
    AUTO = "auto", "Auto (försök känna igen format)"
    GENERIC = "generic", "Generisk CSV"
    SKATTEVERKET = "skatteverket", "Skatteverket (skattekonto CSV)"
    SWEDBANK = "swedbank", "Swedbank / Sparbankerna (otestad)"
    NORDEA = "nordea", "Nordea (otestad)"
    SEB = "seb", "SEB (otestad)"
    HANDELSBANKEN = "handelsbanken", "Handelsbanken"
    DANSKE_BANK = "danske_bank", "Danske Bank (otestad)"
    LANSFORSAKRINGAR = "lansforsakringar", "Länsförsäkringar Bank (otestad)"
    SKANDIABANKEN = "skandiabanken", "Skandia / Skandiabanken (otestad)"
    ICA_BANKEN = "ica_banken", "ICA Banken (otestad)"
    AVANZA = "avanza", "Avanza Bank (otestad)"
    NORDNET = "nordnet", "Nordnet Bank (otestad)"
    SVEA = "svea", "Svea Bank (otestad)"
    RESURS = "resurs", "Resurs Bank (otestad)"
    MARGINALEN = "marginalen", "Marginalen Bank"
    COLLECTOR = "collector", "Collector Bank (otestad)"


class BankAccount(models.Model):
    TAX_BOOKKEEPING_ACCOUNT_NUMBER = "1630"

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="bank_accounts",
        verbose_name="Företag",
    )
    name = models.CharField("Namn", max_length=120)
    account_number = models.CharField("Kontonummer", max_length=64, blank=True)
    account_type = models.CharField("Typ", max_length=20, choices=BankAccountType.choices)
    default_bank_profile = models.CharField(
        "Standardformat CSV",
        max_length=30,
        choices=BankProfile.choices,
        default=BankProfile.AUTO,
    )
    bookkeeping_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="bank_connections",
        verbose_name="Bokföringskonto",
    )
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="uniq_bank_account_name_per_company",
            ),
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(account_type=BankAccountType.TAX),
                name="uniq_tax_bank_account_per_company",
            ),
        ]
        verbose_name = "Bankkälla"
        verbose_name_plural = "Bankkällor"

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"

    def clean(self):
        super().clean()
        if self.account_type != BankAccountType.TAX:
            return

        if self.bookkeeping_account_id is None:
            raise ValidationError({"bookkeeping_account": "Skattekonto måste vara kopplat till konto 1630."})

        if self.bookkeeping_account.company_id != self.company_id:
            raise ValidationError({"bookkeeping_account": "Bokföringskontot måste tillhöra samma företag."})

        if self.bookkeeping_account.number != self.TAX_BOOKKEEPING_ACCOUNT_NUMBER:
            raise ValidationError({"bookkeeping_account": "Skattekonto måste vara kopplat till konto 1630."})


class BankImport(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="bank_imports",
        verbose_name="Företag",
    )
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="imports",
        verbose_name="Bankkälla",
    )
    source_name = models.CharField("Källa", max_length=100, default="CSV")
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Importerad av",
    )
    imported_at = models.DateTimeField("Importerad", auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name = "Import"
        verbose_name_plural = "Importer"

    def __str__(self):
        return f"{self.source_name} - {self.bank_account.name}"


class BankTransaction(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="bank_transactions",
        verbose_name="Företag",
    )
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Bankkälla",
    )
    import_batch = models.ForeignKey(
        BankImport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="Import",
    )
    date = models.DateField("Datum")
    description = models.CharField("Text", max_length=500)
    amount = models.DecimalField("Belopp", max_digits=15, decimal_places=2)
    balance = models.DecimalField(
        "Saldo",
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
    )
    external_id = models.CharField("Externt ID", max_length=120)
    is_booked = models.BooleanField("Bokförd", default=False)
    booked_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_transaction_sources",
        verbose_name="Verifikation",
    )
    booked_at = models.DateTimeField("Bokförd vid", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "bank_account", "external_id"],
                name="uniq_bank_tx_external_id_per_company_account",
            ),
            models.UniqueConstraint(
                fields=["bank_account", "booked_transaction"],
                condition=models.Q(booked_transaction__isnull=False),
                name="uniq_bank_tx_per_account_per_verification",
            ),
        ]
        verbose_name = "Banktransaktion"
        verbose_name_plural = "Banktransaktioner"

    def __str__(self):
        return f"{self.date} {self.description} {self.amount}"

    @property
    def is_credit(self):
        return self.amount < Decimal("0")
