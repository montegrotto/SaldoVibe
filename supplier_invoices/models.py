import json
from decimal import Decimal
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from bookkeeping.payables import AbstractPayment, PayableLabels, PayableMixin


class Supplier(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="suppliers",
        verbose_name="Företag",
    )
    name = models.CharField("Namn", max_length=200)
    org_number = models.CharField("Organisationsnummer", max_length=50, blank=True)
    email = models.EmailField("E-post", blank=True)
    phone = models.CharField("Telefon", max_length=50, blank=True)
    bankgiro = models.CharField("Bankgiro", max_length=50, blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_supplier_name_per_company")]
        verbose_name = "Leverantör"
        verbose_name_plural = "Leverantörer"

    def __str__(self):
        return self.name


class SupplierInvoice(PayableMixin):
    PAYABLE_LABEL = "Leverantörsfaktura"

    COUNTERPARTY_FIELD = "supplier"

    PAYMENT_LABELS = PayableLabels(
        not_bookkept="Fakturan måste vara bokförd innan den kan markeras som betald.",
        already_paid="Fakturan är redan markerad som betald.",
        not_manually_paid="Fakturan är inte manuellt markerad som betald.",
    )

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="supplier_invoices",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        "bookkeeping.AccountingYear",
        on_delete=models.PROTECT,
        related_name="supplier_invoices",
        verbose_name="Räkenskapsår",
    )
    supplier = models.ForeignKey(
        "supplier_invoices.Supplier",
        on_delete=models.PROTECT,
        related_name="invoices",
        null=True,
        blank=True,
        verbose_name="Leverantör",
    )
    supplier_name = models.CharField("Leverantör", max_length=200)
    invoice_number = models.CharField("Fakturanummer", max_length=100, blank=True)
    ocr_code = models.CharField("OCR", max_length=50, blank=True)
    invoice_date = models.DateField("Fakturadatum")
    due_date = models.DateField("Förfallodatum")

    expense_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="supplier_expense_invoices",
        verbose_name="Kostnadskonto",
        null=True,
        blank=True,
    )
    payable_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="supplier_payable_invoices",
        verbose_name="Skuldkonto",
    )
    vat_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="supplier_vat_invoices",
        null=True,
        blank=True,
        verbose_name="Momskonto",
    )

    amount_ex_vat = models.DecimalField("Belopp exkl. moms", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField("Totalbelopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    vat_amount = models.DecimalField("Moms", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    attachments = models.ManyToManyField(
        "attachments.TransactionAttachment",
        related_name="supplier_invoices",
        blank=True,
        verbose_name="Bilagor",
    )

    is_registered = models.BooleanField("Bokförd", default=False)
    registered_at = models.DateTimeField("Bokförd vid", null=True, blank=True)
    registered_transaction = models.OneToOneField(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_invoice",
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
        related_name="supplier_invoice_payments",
        verbose_name="Betalkonto",
    )
    payment_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_invoice_payment_transactions",
        verbose_name="Senaste betalningsverifikation",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_supplier_invoices",
        verbose_name="Skapad av",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "invoice_number"],
                condition=~Q(invoice_number=""),
                name="uniq_supplier_invoice_number_per_company",
            )
        ]
        verbose_name = "Leverantörsfaktura"
        verbose_name_plural = "Leverantörsfakturor"

    def __str__(self):
        invoice_label = self.invoice_number or "Utan fakturanummer"
        return f"{invoice_label} - {self.supplier_display_name}"

    def _build_payment_qr_payload(self):
        reference = (self.ocr_code or self.invoice_number or "").strip()
        due_digits = self.due_date.strftime("%Y%m%d") if self.due_date else ""
        supplier_org_number = ""
        bankgiro = ""
        if self.supplier_id and self.supplier:
            supplier_org_number = (self.supplier.org_number or "").strip()
            bankgiro = (self.supplier.bankgiro or "").strip()

        return {
            "uqr": 2,
            "tp": 1,
            "nme": self.supplier_display_name,
            "cid": supplier_org_number,
            "iref": reference,
            "ddt": due_digits,
            "due": float((self.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))),
            "pt": "BG",
            "acc": bankgiro,
        }

    def build_payment_qr_svg(self):
        payload = self._build_payment_qr_payload()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        try:
            import qrcode
            from qrcode.image.svg import SvgPathImage

            image = qrcode.make(payload_json, image_factory=SvgPathImage, box_size=10, border=2)
            return image.to_string(encoding="unicode")
        except Exception:
            # Fallback keeps invoice save working even if QR library is missing.
            pass

        ocr_text = escape((self.ocr_code or "").strip())
        amount_text = f"{(self.total_amount or Decimal('0.00')):.2f} kr"
        due_date_text = self.due_date.isoformat() if self.due_date else "-"
        bankgiro_value = "-"
        if self.supplier_id and self.supplier and self.supplier.bankgiro:
            bankgiro_value = self.supplier.bankgiro.strip()
        bankgiro_text = escape(bankgiro_value)

        return f"""<svg xmlns='http://www.w3.org/2000/svg' width='680' height='300' viewBox='0 0 680 300'>
  <rect x='0' y='0' width='680' height='300' fill='#ffffff'/>
  <rect x='10' y='10' width='660' height='280' rx='8' ry='8' fill='#f8f9fa' stroke='#ced4da' stroke-width='1'/>
  <text x='28' y='44' fill='#495057' font-size='18' font-family='Arial, Helvetica, sans-serif'>OCR-betalningsuppgifter</text>
    <text x='28' y='78' fill='#6c757d' font-size='13' font-family='Arial, Helvetica, sans-serif'>Belopp: {amount_text}</text>
    <text x='28' y='102' fill='#6c757d' font-size='13' font-family='Arial, Helvetica, sans-serif'>Bankgiro: {bankgiro_text}</text>
    <text x='28' y='126' fill='#6c757d' font-size='13' font-family='Arial, Helvetica, sans-serif'>Förfallodatum: {due_date_text}</text>
  <rect x='24' y='170' width='632' height='104' fill='#ffffff' stroke='#adb5bd' stroke-width='1'/>
  <text x='40' y='228' fill='#212529' font-size='34' letter-spacing='3' font-family='Courier New, monospace'>{ocr_text}</text>
</svg>"""

    @property
    def supplier_display_name(self):
        if self.supplier_id:
            return self.supplier.name
        return self.supplier_name

    @property
    def cost_amount_total(self):
        lines_total = sum((line.debit - line.credit for line in self.cost_lines.all()), Decimal("0.00"))
        if lines_total > Decimal("0.00"):
            return lines_total
        return self.amount_ex_vat or Decimal("0.00")

    def clean(self):
        super().clean()

        if self.due_date and self.invoice_date and self.due_date < self.invoice_date:
            raise ValidationError("Förfallodatum kan inte vara före fakturadatum.")

        if self.amount_ex_vat < Decimal("0"):
            raise ValidationError("Belopp exkl. moms måste vara 0 eller större.")

        if self.total_amount <= Decimal("0"):
            raise ValidationError("Totalbelopp måste vara större än 0.")

        if self.vat_amount < Decimal("0"):
            raise ValidationError("Moms måste vara 0 eller större.")

        if self.vat_amount > Decimal("0") and not self.vat_account_id:
            raise ValidationError("Momskonto krävs när momsbelopp är större än 0.")

        if self.supplier_id and self.company_id and self.supplier.company_id != self.company_id:
            raise ValidationError("Vald leverantör tillhör ett annat företag.")

    def register_and_bookkeep(self, user):
        from .services import register_and_bookkeep_supplier_invoice

        return register_and_bookkeep_supplier_invoice(self, user)

    def payment_settlement(self):
        # An outgoing payment debits the payable; a credit invoice reverses that.
        from banking.services import expected_supplier_payment_sign

        side = "debit" if expected_supplier_payment_sign(invoice=self) < 0 else "credit"
        return self.payable_account, side


class SupplierInvoicePayment(AbstractPayment):
    payable = models.ForeignKey(
        SupplierInvoice,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Leverantörsfaktura",
    )
    transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_invoice_payment_rows",
        verbose_name="Verifikation",
    )
    payment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="supplier_invoice_payment_rows",
        verbose_name="Betalkonto",
    )

    class Meta(AbstractPayment.Meta):
        verbose_name = "Leverantörsfakturabetalning"
        verbose_name_plural = "Leverantörsfakturabetalningar"


class SupplierInvoiceCostLine(models.Model):
    invoice = models.ForeignKey(
        SupplierInvoice,
        on_delete=models.CASCADE,
        related_name="cost_lines",
        verbose_name="Leverantörsfaktura",
    )
    expense_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        related_name="supplier_invoice_cost_lines",
        verbose_name="Kostnadskonto",
    )
    debit = models.DecimalField("Debet", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    credit = models.DecimalField("Kredit", max_digits=15, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["id"]
        verbose_name = "Fakturarad"
        verbose_name_plural = "Fakturarader"
        constraints = [
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="supplier_invoice_cost_line_not_both_debit_and_credit",
            ),
        ]

    def __str__(self):
        side = "Debet" if self.debit else "Kredit"
        return f"{self.expense_account.number} {side} {self.debit or self.credit}"
