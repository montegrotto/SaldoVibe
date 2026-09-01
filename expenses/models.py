from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from bookkeeping.payables import AbstractPayment, PayableLabels, PayableMixin


class ExpenseClaim(PayableMixin):
    """Ett utlägg: någon har betalat en företagskostnad privat och ska ersättas.

    Registreras som skuld (2820) vid bokföring och regleras sedan via bankbetalning
    eller manuell betalningsregistrering, med delbetalningar och historik som fakturor.
    """

    PAYABLE_LABEL = "Utlägg"

    PAYMENT_LABELS = PayableLabels(
        not_bookkept="Utlägget måste vara bokfört innan det kan markeras som betalt.",
        already_paid="Utlägget är redan markerat som betalt.",
        not_manually_paid="Utlägget är inte manuellt markerat som betalt.",
        payment_registered="Utbetalningen har registrerats och bokförts.",
    )

    PAYMENT_STATUS_BADGES = {
        **PayableMixin.PAYMENT_STATUS_BADGES,
        "paid": ("Utbetald", "bg-success"),
        "partial": ("Delvis utbetald", "bg-info text-dark"),
    }
    DEFAULT_LIABILITY_ACCOUNT_NUMBERS = ("2820", "2890")
    DEFAULT_VAT_ACCOUNT_NUMBER = "2640"

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="expense_claims",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        "bookkeeping.AccountingYear",
        on_delete=models.PROTECT,
        related_name="expense_claims",
        verbose_name="Räkenskapsår",
    )
    employee = models.ForeignKey(
        "payroll.Employee",
        on_delete=models.PROTECT,
        related_name="expense_claims",
        null=True,
        blank=True,
        verbose_name="Anställd",
    )
    person_name = models.CharField("Namn", max_length=200, blank=True)
    description = models.CharField("Beskrivning", max_length=255)
    expense_date = models.DateField("Utläggsdatum")

    expense_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="expense_claim_costs",
        verbose_name="Kostnadskonto",
    )
    liability_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="expense_claim_liabilities",
        verbose_name="Skuldkonto",
    )
    vat_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="expense_claim_vat",
        null=True,
        blank=True,
        verbose_name="Momskonto",
    )

    amount_ex_vat = models.DecimalField("Belopp exkl. moms", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    vat_amount = models.DecimalField("Moms", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField("Totalbelopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    attachments = models.ManyToManyField(
        "attachments.TransactionAttachment",
        related_name="expense_claims",
        blank=True,
        verbose_name="Kvitton",
    )

    is_registered = models.BooleanField("Bokförd", default=False)
    registered_at = models.DateTimeField("Bokförd vid", null=True, blank=True)
    registered_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expense_claims",
        verbose_name="Verifikation",
    )

    is_paid = models.BooleanField("Utbetald", default=False)
    paid_amount = models.DecimalField("Utbetalt belopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    paid_at = models.DateTimeField("Utbetald vid", null=True, blank=True)
    payment_date = models.DateField("Utbetalningsdatum", null=True, blank=True)
    payment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_claim_payments",
        verbose_name="Betalkonto",
    )
    payment_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expense_claim_payment_transactions",
        verbose_name="Senaste betalningsverifikation",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_expense_claims",
        verbose_name="Skapad av",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-created_at"]
        verbose_name = "Utlägg"
        verbose_name_plural = "Utlägg"

    def __str__(self):
        return f"{self.description} - {self.person_display_name}"

    @property
    def person_display_name(self):
        if self.employee_id:
            return str(self.employee)
        return self.person_name

    def clean(self):
        super().clean()

        if not self.employee_id and not (self.person_name or "").strip():
            raise ValidationError("Ange anställd eller namn på den som gjort utlägget.")

        if self.total_amount is not None and self.total_amount <= Decimal("0.00"):
            raise ValidationError("Totalbelopp måste vara större än 0.")

        if self.vat_amount is not None and self.vat_amount < Decimal("0.00"):
            raise ValidationError("Moms måste vara 0 eller större.")

        if self.total_amount is not None and self.vat_amount is not None and self.vat_amount > self.total_amount:
            raise ValidationError("Moms kan inte vara större än totalbeloppet.")

        if self.vat_amount and self.vat_amount > Decimal("0") and not self.vat_account_id:
            raise ValidationError("Momskonto krävs när momsbelopp är större än 0.")

        if self.employee_id and self.company_id and self.employee.company_id != self.company_id:
            raise ValidationError("Vald anställd tillhör ett annat företag.")

    def register_and_bookkeep(self, user):
        from .services import register_and_bookkeep_expense_claim

        return register_and_bookkeep_expense_claim(self, user)

    def payment_settlement(self):
        # Utlägg är alltid en skuld till den som gjort utlägget: utbetalning debiterar skuldkontot.
        return self.liability_account, "debit"


class ExpenseClaimPayment(AbstractPayment):
    payable = models.ForeignKey(
        ExpenseClaim,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Utlägg",
    )
    transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expense_claim_payment_rows",
        verbose_name="Verifikation",
    )
    payment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expense_claim_payment_rows",
        verbose_name="Betalkonto",
    )

    class Meta(AbstractPayment.Meta):
        verbose_name = "Utläggsbetalning"
        verbose_name_plural = "Utläggsbetalningar"
