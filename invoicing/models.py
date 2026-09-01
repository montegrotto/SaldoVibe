import base64
import calendar
import json
import re
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction

from bookkeeping.payables import AbstractPayment, PayableLabels, PayableMixin, quantize_amount


class Customer(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="customers",
        verbose_name="Företag",
    )
    name = models.CharField("Kundnamn", max_length=200)
    org_number = models.CharField("Organisationsnummer", max_length=50, blank=True)
    vat_number = models.CharField("Momsregistreringsnummer", max_length=50, blank=True)
    email = models.EmailField("E-post", blank=True)
    phone = models.CharField("Telefon", max_length=50, blank=True)
    address = models.CharField("Adress", max_length=255, blank=True)
    postal_code = models.CharField("Postnummer", max_length=20, blank=True)
    city = models.CharField("Ort", max_length=120, blank=True)
    country_code = models.CharField("Landskod (ISO 3166-1 alpha-2)", max_length=2, default="SE", blank=True)
    default_payment_terms_days = models.PositiveIntegerField("Standard betalningsvillkor (dagar)", default=30)
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_customer_name_per_company"),
        ]
        verbose_name = "Kund"
        verbose_name_plural = "Kunder"

    def __str__(self):
        return self.name


class Article(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="articles",
        verbose_name="Företag",
    )
    article_number = models.CharField("Artikelnummer", max_length=50, blank=True)
    name = models.CharField("Artikel", max_length=200)
    description = models.TextField("Beskrivning", blank=True)
    unit = models.CharField("Enhet", max_length=20, default="st")
    unit_price = models.DecimalField("Á-pris", max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField("Moms %", max_digits=5, decimal_places=2, default=Decimal("25.00"))
    income_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_articles",
        verbose_name="Intäktskonto",
    )
    is_active = models.BooleanField("Aktiv", default=True)

    class Meta:
        ordering = ["article_number", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "article_number"],
                condition=~models.Q(article_number=""),
                name="uniq_article_number_per_company",
            ),
        ]
        verbose_name = "Artikel"
        verbose_name_plural = "Artiklar"

    def __str__(self):
        return self.name


class InvoiceNumberSequence(models.Model):
    """Race-safe per-company/year counter backing Invoice.invoice_number allocation."""

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="invoice_number_sequences",
    )
    year = models.PositiveIntegerField()
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "year"], name="uniq_invoice_number_sequence_per_company_year"),
        ]


class Invoice(PayableMixin):
    # Customer invoices call the ledger flag is_booked; the purchase-side documents
    # call theirs is_registered.
    BOOKKEPT_FIELD = "is_booked"

    PAYABLE_LABEL = "Kundfaktura"

    PAYMENT_WRITE_OFF_DEFAULT_ACCOUNT = "6351"
    PAYMENT_VAT_ADJUST = True
    COUNTERPARTY_FIELD = "customer"

    PAYMENT_LABELS = PayableLabels(
        not_bookkept="Fakturan måste vara bokförd innan den kan markeras som betald.",
        already_paid="Fakturan är redan markerad som betald.",
        not_manually_paid="Fakturan är inte manuellt markerad som betald.",
    )

    PAYMENT_STATUS_BADGES = {
        "paid": ("Betald", "bg-primary-subtle text-primary-emphasis"),
        "partial": ("Delbetald", "bg-info-subtle text-info-emphasis"),
        "bookkept": ("Bokförd", "bg-success-subtle text-success-emphasis"),
        "draft": ("Utkast", "bg-secondary-subtle text-secondary-emphasis"),
    }

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="outgoing_invoices",
        verbose_name="Företag",
    )
    customer = models.ForeignKey(
        "invoicing.Customer",
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name="Kund",
    )
    invoice_number = models.CharField("Fakturanummer", max_length=30, blank=True)
    ocr_code = models.CharField("OCR", max_length=50, blank=True)
    invoice_date = models.DateField("Fakturadatum")
    due_date = models.DateField("Förfallodatum")
    delivery_date = models.DateField(
        "Leverans-/tillhandahållandedatum",
        null=True,
        blank=True,
        help_text="Ange endast om leveransdatumet skiljer sig från fakturadatumet (krav enligt 11 kap. mervärdesskattelagen).",
    )
    payment_terms_days = models.PositiveIntegerField("Betalningsvillkor (dagar)", default=30)
    currency = models.CharField("Valuta", max_length=3, default="SEK")
    reference = models.CharField("Er referens", max_length=120, blank=True)
    reverse_charge = models.BooleanField(
        "Omvänd betalningsskyldighet",
        default=False,
        help_text="Köparen är skattskyldig för momsen. Fakturan får då inte innehålla något momsbelopp.",
    )
    notes = models.TextField("Notering", blank=True)
    accounting_year = models.ForeignKey(
        "bookkeeping.AccountingYear",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_invoices",
        verbose_name="Räkenskapsår",
    )
    receivable_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="customer_receivable_invoices",
        verbose_name="Kundfordringskonto",
    )
    is_booked = models.BooleanField("Bokförd", default=False)
    booked_at = models.DateTimeField("Bokförd vid", null=True, blank=True)
    booked_transaction = models.OneToOneField(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_invoice",
        verbose_name="Verifikation",
    )
    is_paid = models.BooleanField("Betald", default=False)
    paid_amount = models.DecimalField("Betalt belopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    paid_at = models.DateTimeField("Betald vid", null=True, blank=True)
    payment_date = models.DateField("Betalningsdatum", null=True, blank=True)
    payment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="customer_invoice_payments",
        verbose_name="Betalkonto",
    )
    payment_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_invoice_payments",
        verbose_name="Senaste betalningsverifikation",
    )
    recurring_invoice = models.ForeignKey(
        "invoicing.RecurringInvoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_invoices",
        verbose_name="Återkommande mall",
    )
    recurring_period_label = models.CharField("Period", max_length=100, blank=True)
    attachments = models.ManyToManyField(
        "attachments.TransactionAttachment",
        related_name="sales_invoices",
        blank=True,
        verbose_name="Bilagor",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "invoice_number"], name="uniq_invoice_number_per_company"),
        ]
        verbose_name = "Kundfaktura"
        verbose_name_plural = "Kundfakturor"

    def __str__(self):
        return self.invoice_number or f"Faktura {self.pk}"

    def clean(self):
        super().clean()
        if self.due_date and self.invoice_date and self.due_date < self.invoice_date:
            raise ValidationError("Förfallodatum kan inte vara före fakturadatum.")
        if self.reverse_charge and self.pk and self.vat_amount != Decimal("0.00"):
            raise ValidationError(
                "Vid omvänd betalningsskyldighet får fakturan inte innehålla något momsbelopp. Sätt momssatsen till 0% på raderna."
            )

    def save(self, *args, **kwargs):
        with db_transaction.atomic():
            if not self.invoice_number:
                self.invoice_number = self._generate_invoice_number()
            self.ocr_code = self._generate_ocr_code()
            super().save(*args, **kwargs)

    def _generate_invoice_number(self):
        if not self.invoice_date:
            year = 0
        elif hasattr(self.invoice_date, "year"):
            year = self.invoice_date.year
        else:
            try:
                year = int(str(self.invoice_date)[:4])
            except (TypeError, ValueError):
                year = 0
        prefix = f"{year}-"

        sequence, created = InvoiceNumberSequence.objects.select_for_update().get_or_create(
            company=self.company,
            year=year,
        )
        if created:
            latest = (
                type(self)
                .objects.filter(company=self.company, invoice_number__startswith=prefix)
                .order_by("-invoice_number")
                .values_list("invoice_number", flat=True)
                .first()
            )
            try:
                sequence.next_number = int(latest.split("-")[-1]) + 1 if latest else 1
            except (TypeError, ValueError):
                sequence.next_number = 1

        seq_number = sequence.next_number
        sequence.next_number = seq_number + 1
        sequence.save(update_fields=["next_number"])
        return f"{prefix}{seq_number:04d}"

    def _generate_ocr_code(self):
        base_digits = re.sub(r"\D", "", (self.invoice_number or "").strip())
        if not base_digits:
            return ""
        return f"{base_digits}{self._calculate_mod10_check_digit(base_digits)}"

    @staticmethod
    def _calculate_mod10_check_digit(value):
        total = 0
        multiplier = 2
        for digit_char in reversed(value):
            digit = int(digit_char) * multiplier
            total += digit // 10 + digit % 10
            multiplier = 1 if multiplier == 2 else 2
        return str((10 - (total % 10)) % 10)

    @property
    def item_lines(self):
        return [line for line in self.lines.all() if line.line_type == InvoiceLine.LINE_TYPE_ITEM]

    @property
    def subtotal_ex_vat(self):
        return sum((line.line_total_ex_vat for line in self.item_lines), Decimal("0.00"))

    @property
    def vat_amount(self):
        return sum((line.line_vat_amount for line in self.item_lines), Decimal("0.00"))

    @property
    def total_amount(self):
        return self.subtotal_ex_vat + self.vat_amount

    @property
    def is_credit_invoice(self):
        return self._amount(self.total_amount) < Decimal("0.00")

    def payment_settlement(self):
        # An incoming payment credits the receivable; a credit invoice reverses that.
        from banking.services import expected_customer_payment_sign

        side = "credit" if expected_customer_payment_sign(invoice=self) > 0 else "debit"
        return self.receivable_account, side

    @property
    def vat_summary(self):
        summary = {}
        for line in self.item_lines:
            rate_key = str(line.vat_rate)
            if rate_key not in summary:
                summary[rate_key] = {
                    "rate": line.vat_rate,
                    "base": Decimal("0.00"),
                    "vat": Decimal("0.00"),
                }
            summary[rate_key]["base"] += line.line_total_ex_vat
            summary[rate_key]["vat"] += line.line_vat_amount
        return sorted(summary.values(), key=lambda item: item["rate"])

    def build_payment_qr_payload(self, *, amount=None, due_date=None):
        """amount/due_date överstyr fakturans egna värden — påminnelsen betalas
        med återstående belopp + avgift till ett nytt datum."""
        payment_reference = (self.ocr_code or self.invoice_number or self.reference or "").strip()
        payment_code = "BG"
        payment_account = ""
        if amount is None:
            amount = self.total_amount
        if due_date is None:
            due_date = self.due_date
        if not due_date:
            due_digits = ""
        elif hasattr(due_date, "strftime"):
            due_digits = due_date.strftime("%Y%m%d")
        else:
            try:
                due_digits = f"{int(str(due_date)[:4]):04d}{int(str(due_date)[5:7]):02d}{int(str(due_date)[8:10]):02d}"
            except (TypeError, ValueError, IndexError):
                due_digits = ""
        if self.company.bankgiro:
            payment_account = self.company.bankgiro.strip()
            payment_code = "BG"
        elif self.company.plusgiro:
            payment_account = self.company.plusgiro.strip()
            payment_code = "PG"

        return {
            "uqr": 2,
            "tp": 1,
            "nme": self.company.name,
            "cid": (self.company.org_number or "").strip(),
            "iref": payment_reference,
            "ddt": due_digits,
            "due": float((amount or Decimal("0.00")).quantize(Decimal("0.01"))),
            "pt": payment_code,
            "acc": payment_account,
        }

    def build_payment_qr_png(self, *, amount=None, due_date=None):
        """PNG som data-URI — PDF-mallen renderas med xhtml2pdf som inte klarar inline-SVG."""
        payload_json = json.dumps(
            self.build_payment_qr_payload(amount=amount, due_date=due_date),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            import qrcode

            image = qrcode.make(payload_json, box_size=10, border=2)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""

    def bookkeep(self, user):
        from .services import bookkeep_invoice

        return bookkeep_invoice(self, user)


class InvoicePayment(AbstractPayment):
    payable = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Faktura",
    )
    transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_invoice_payment_rows",
        verbose_name="Verifikation",
    )
    payment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="customer_invoice_payment_rows",
        verbose_name="Betalkonto",
    )

    class Meta(AbstractPayment.Meta):
        verbose_name = "Fakturabetalning"
        verbose_name_plural = "Fakturabetalningar"


class InvoiceReminder(models.Model):
    """En utskriven betalningspåminnelse. Bokför ingenting — historiken finns för
    att man ska se att (och när) en påminnelse gjorts, och kunna skriva ut nästa
    med rätt nummer (påminnelse 1, 2, ...)."""

    invoice = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.CASCADE,
        related_name="reminders",
        verbose_name="Faktura",
    )
    fee = models.DecimalField("Påminnelseavgift", max_digits=10, decimal_places=2, default=Decimal("0.00"))
    pay_by_date = models.DateField("Betala senast")
    created_at = models.DateTimeField("Skapad", auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Skapad av",
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Betalningspåminnelse"
        verbose_name_plural = "Betalningspåminnelser"

    def __str__(self):
        return f"Påminnelse {self.invoice} ({self.pay_by_date})"

    @property
    def sequence_number(self):
        """1 för första påminnelsen på fakturan, 2 för nästa, osv."""
        return type(self).objects.filter(invoice_id=self.invoice_id, pk__lte=self.pk).count()


class InvoiceLine(models.Model):
    LINE_TYPE_ITEM = "item"
    LINE_TYPE_TEXT = "text"
    LINE_TYPE_CHOICES = [
        (LINE_TYPE_ITEM, "Artikelrad"),
        (LINE_TYPE_TEXT, "Textrad"),
    ]

    invoice = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="Faktura",
    )
    article = models.ForeignKey(
        "invoicing.Article",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_lines",
        verbose_name="Artikel",
    )
    description = models.CharField("Beskrivning", max_length=255)
    quantity = models.DecimalField("Antal", max_digits=12, decimal_places=2, default=Decimal("1.00"))
    unit = models.CharField("Enhet", max_length=20, default="st")
    unit_price = models.DecimalField("Á-pris", max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField("Moms %", max_digits=5, decimal_places=2, default=Decimal("25.00"))
    line_type = models.CharField("Radtyp", max_length=10, choices=LINE_TYPE_CHOICES, default=LINE_TYPE_ITEM)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Fakturarad"
        verbose_name_plural = "Fakturarader"

    def __str__(self):
        return self.description

    @property
    def line_total_ex_vat(self):
        # Rounded per line, in the same order bookkeep_invoice rounds, so the
        # invoice total always equals what lands on the receivable account.
        return quantize_amount((self.quantity or Decimal("0.00")) * (self.unit_price or Decimal("0.00")))

    @property
    def line_vat_amount(self):
        return quantize_amount(self.line_total_ex_vat * ((self.vat_rate or Decimal("0.00")) / Decimal("100")))


class RecurringInvoiceInterval(models.TextChoices):
    MONTHLY = "monthly", "Månadsvis"
    QUARTERLY = "quarterly", "Kvartalsvis"
    SEMIANNUAL = "semiannual", "Halvårsvis"
    ANNUAL = "annual", "Årsvis"
    CUSTOM_MONTHS = "custom_months", "Eget antal månader"


class RecurringInvoiceDueDateMode(models.TextChoices):
    DAYS_AFTER = "days_after", "Antal dagar efter fakturadatum"
    DAY_OF_MONTH = "day_of_month", "Fast dag i månaden"


class RecurringInvoicePeriodReference(models.TextChoices):
    PREVIOUS = "previous", "Föregående period"
    CURRENT = "current", "Aktuell period"
    NEXT = "next", "Kommande period"


_RECURRING_INTERVAL_MONTHS = {
    RecurringInvoiceInterval.MONTHLY: 1,
    RecurringInvoiceInterval.QUARTERLY: 3,
    RecurringInvoiceInterval.SEMIANNUAL: 6,
    RecurringInvoiceInterval.ANNUAL: 12,
}

_PERIOD_REFERENCE_OFFSET = {
    RecurringInvoicePeriodReference.PREVIOUS: -1,
    RecurringInvoicePeriodReference.CURRENT: 0,
    RecurringInvoicePeriodReference.NEXT: 1,
}

SWEDISH_MONTH_NAMES = [
    "Januari",
    "Februari",
    "Mars",
    "April",
    "Maj",
    "Juni",
    "Juli",
    "Augusti",
    "September",
    "Oktober",
    "November",
    "December",
]


class RecurringInvoice(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="recurring_invoices",
        verbose_name="Företag",
    )
    customer = models.ForeignKey(
        "invoicing.Customer",
        on_delete=models.PROTECT,
        related_name="recurring_invoices",
        verbose_name="Kund",
    )
    name = models.CharField("Mallnamn", max_length=200)
    interval = models.CharField(
        "Intervall",
        max_length=20,
        choices=RecurringInvoiceInterval.choices,
        default=RecurringInvoiceInterval.MONTHLY,
    )
    custom_interval_months = models.PositiveIntegerField("Eget antal månader", null=True, blank=True)
    start_date = models.DateField("Startdatum")
    anchor_to_month_end = models.BooleanField("Alltid sista dagen i månaden", default=False)
    next_run_date = models.DateField("Nästa fakturadatum")
    end_date = models.DateField("Slutdatum", null=True, blank=True)
    due_date_mode = models.CharField(
        "Förfallodatum",
        max_length=20,
        choices=RecurringInvoiceDueDateMode.choices,
        default=RecurringInvoiceDueDateMode.DAYS_AFTER,
    )
    payment_terms_days = models.PositiveIntegerField("Betalningsvillkor (dagar)", default=30)
    due_date_day_of_month = models.PositiveIntegerField("Dag i månaden", null=True, blank=True)
    due_date_last_day_of_month = models.BooleanField("Sista dagen i månaden", default=False)
    period_reference = models.CharField(
        "Period",
        max_length=10,
        choices=RecurringInvoicePeriodReference.choices,
        default=RecurringInvoicePeriodReference.CURRENT,
    )
    reference = models.CharField("Er referens", max_length=120, blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    occurrences_generated = models.PositiveIntegerField(default=0)
    last_generated_at = models.DateTimeField("Senast genererad", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_recurring_invoice_name_per_company"),
        ]
        verbose_name = "Återkommande faktura"
        verbose_name_plural = "Återkommande fakturor"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.interval == RecurringInvoiceInterval.CUSTOM_MONTHS and not self.custom_interval_months:
            raise ValidationError("Ange eget antal månader för det valda intervallet.")
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError("Slutdatum kan inte vara före startdatum.")
        if self.due_date_mode == RecurringInvoiceDueDateMode.DAY_OF_MONTH and not (
            self.due_date_last_day_of_month or self.due_date_day_of_month
        ):
            raise ValidationError("Ange vilken dag i månaden fakturan ska förfalla.")

    @property
    def interval_months(self):
        if self.interval == RecurringInvoiceInterval.CUSTOM_MONTHS:
            return self.custom_interval_months or 1
        return _RECURRING_INTERVAL_MONTHS.get(self.interval, 1)

    @staticmethod
    def _date_in_month(date_value, months_offset=0, *, day=None, use_month_end=False):
        month_index = date_value.month - 1 + months_offset
        year = date_value.year + month_index // 12
        month = month_index % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        target_day = last_day if use_month_end else min(day or date_value.day, last_day)
        return date_value.replace(year=year, month=month, day=target_day)

    def _target_run_date(self, occurrence_index):
        months = occurrence_index * self.interval_months
        return self._date_in_month(
            self.start_date, months, day=self.start_date.day, use_month_end=self.anchor_to_month_end
        )

    def _compute_due_date(self, invoice_date):
        if self.due_date_mode == RecurringInvoiceDueDateMode.DAY_OF_MONTH:
            due_date = self._date_in_month(
                invoice_date, day=self.due_date_day_of_month, use_month_end=self.due_date_last_day_of_month
            )
            if due_date < invoice_date:
                due_date = self._date_in_month(
                    invoice_date,
                    1,
                    day=self.due_date_day_of_month,
                    use_month_end=self.due_date_last_day_of_month,
                )
            return due_date
        return invoice_date + timedelta(days=self.payment_terms_days)

    @staticmethod
    def _format_period_label(period_start, period_end):
        spans_whole_month = (
            period_start.day == 1
            and period_start.year == period_end.year
            and period_start.month == period_end.month
            and period_end.day == calendar.monthrange(period_end.year, period_end.month)[1]
        )
        if spans_whole_month:
            return f"{SWEDISH_MONTH_NAMES[period_start.month - 1]} {period_start.year}"
        return f"{period_start:%Y-%m-%d} – {period_end:%Y-%m-%d}"

    @property
    def is_due(self):
        from django.utils import timezone

        return self.is_active and self.next_run_date <= timezone.localdate()

    def generate_invoice(self):
        from django.db import transaction as db_transaction
        from django.utils import timezone

        if not self.is_active:
            raise ValidationError("Mallen är pausad och kan inte generera fakturor.")
        if self.end_date and self.next_run_date > self.end_date:
            raise ValidationError("Mallens slutdatum har passerats.")

        invoice_date = self.next_run_date
        next_occurrence_date = self._target_run_date(self.occurrences_generated + 1)
        due_date = self._compute_due_date(invoice_date)

        period_offset = _PERIOD_REFERENCE_OFFSET.get(self.period_reference, 0)
        referenced_occurrence_index = self.occurrences_generated + period_offset
        period_start = self._target_run_date(referenced_occurrence_index)
        period_end = self._target_run_date(referenced_occurrence_index + 1) - timedelta(days=1)
        period_label = self._format_period_label(period_start, period_end)

        with db_transaction.atomic():
            invoice = Invoice.objects.create(
                company=self.company,
                customer=self.customer,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_terms_days=self.payment_terms_days,
                reference=self.reference,
                recurring_invoice=self,
                recurring_period_label=period_label,
            )

            for template_line in self.lines.all():
                description = template_line.description.replace("{period}", period_label)
                invoice.lines.create(
                    article=template_line.article,
                    description=description,
                    quantity=template_line.quantity,
                    unit=template_line.unit,
                    unit_price=template_line.unit_price,
                    vat_rate=template_line.vat_rate,
                    line_type=template_line.line_type,
                    sort_order=template_line.sort_order,
                )

            self.next_run_date = next_occurrence_date
            self.occurrences_generated += 1
            self.last_generated_at = timezone.now()
            self.save(update_fields=["next_run_date", "occurrences_generated", "last_generated_at"])

        return invoice


class RecurringInvoiceLine(models.Model):
    recurring_invoice = models.ForeignKey(
        RecurringInvoice,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="Mall",
    )
    article = models.ForeignKey(
        "invoicing.Article",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_invoice_lines",
        verbose_name="Artikel",
    )
    description = models.CharField("Beskrivning", max_length=255)
    quantity = models.DecimalField("Antal", max_digits=12, decimal_places=2, default=Decimal("1.00"))
    unit = models.CharField("Enhet", max_length=20, default="st")
    unit_price = models.DecimalField("Á-pris", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_rate = models.DecimalField("Moms %", max_digits=5, decimal_places=2, default=Decimal("25.00"))
    line_type = models.CharField(
        "Radtyp",
        max_length=10,
        choices=InvoiceLine.LINE_TYPE_CHOICES,
        default=InvoiceLine.LINE_TYPE_ITEM,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Mallrad"
        verbose_name_plural = "Mallrader"

    def __str__(self):
        return self.description


def get_due_alert_state_for_company(company):
    """Return recurring invoice templates ready to generate, count, and a deterministic signature."""
    if company is None:
        return [], 0, ""

    templates = RecurringInvoice.objects.filter(company=company, is_active=True).order_by("pk")
    due_templates = [template for template in templates if template.is_due]
    signature_parts = [f"{template.pk}:{template.next_run_date.isoformat()}" for template in due_templates]
    signature = "|".join(signature_parts)
    return due_templates, len(due_templates), signature
