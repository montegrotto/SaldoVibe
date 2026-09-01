import hashlib
import json
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

from saldovibe.encryption import EncryptedTextField, blind_index

from .skatteverket_api import get_tax_amount_from_skatteverket

MONEY_QUANT = Decimal("0.01")


class AdjustmentCategory(models.TextChoices):
    OTHER = "", "Övrigt"
    TAXABLE_BENEFIT = "taxable_benefit", "Skattepliktig förmån"
    OB_SUPPLEMENT = "ob_supplement", "OB-tillägg"
    OVERTIME = "overtime", "Övertidsersättning"
    BONUS = "bonus", "Bonus"
    GROSS_SALARY_DEDUCTION = "gross_salary_deduction", "Bruttolöneavdrag"
    TAX_FREE_MILEAGE = "tax_free_mileage", "Skattefri milersättning"
    NET_SALARY_DEDUCTION = "net_salary_deduction", "Nettolöneavdrag"


ADJUSTMENT_ACCOUNT_MAP = {
    AdjustmentCategory.TAX_FREE_MILEAGE: "7331",
}


def quantize_money(value):
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def quantize_whole_kronor_down(value):
    whole_kronor = Decimal(value).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return whole_kronor.quantize(MONEY_QUANT)


def get_adjustment_account_number(adjustment):
    if (
        adjustment.phase == SalaryAdjustment.Phase.POST_TAX
        and adjustment.direction == SalaryAdjustment.Direction.DEDUCTION
    ):
        return "7388"
    return ADJUSTMENT_ACCOUNT_MAP.get(adjustment.category, "7010")


class TaxTableColumn(models.IntegerChoices):
    COL_1 = 1, "Kolumn 1 - Lön, arvode och liknande ersättning (huvudinkomst)"
    COL_2 = 2, "Kolumn 2 - Pension och andra pensionsgrundande ersättningar"
    COL_3 = 3, "Kolumn 3 - Lön till personer som fyllt 66 år vid årets ingång"
    COL_4 = 4, "Kolumn 4 - Pension till personer som fyllt 66 år vid årets ingång"
    COL_5 = 5, "Kolumn 5 - Andra skattepliktiga ersättningar"
    COL_6 = 6, "Kolumn 6 - Särskilda fall enligt Skatteverkets tabell"


class Employee(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="employees",
        verbose_name="Företag",
    )
    first_name = models.CharField("Förnamn", max_length=120)
    last_name = models.CharField("Efternamn", max_length=120)
    personal_identity_number = EncryptedTextField("Personnummer", validators=[MaxLengthValidator(12)])
    # HMAC-index (saldovibe.encryption.blind_index) — krypteringen är
    # icke-deterministisk, så unikhetskontrollen behöver den här kolumnen.
    personal_identity_number_hash = models.CharField(max_length=64, editable=False, default="")
    address = models.CharField("Adress", max_length=255, blank=True)
    postal_code = models.CharField("Postnummer", max_length=20, blank=True)
    city = models.CharField("Ort", max_length=100, blank=True)
    monthly_salary = models.DecimalField("Månadslön", max_digits=12, decimal_places=2)
    employment_rate = models.DecimalField(
        "Sysselsättningsgrad (%)", max_digits=5, decimal_places=2, default=Decimal("100.00")
    )
    tax_table_number = models.PositiveSmallIntegerField("Skattetabell", default=32)
    tax_table_column = models.PositiveSmallIntegerField(
        "Kolumn", choices=TaxTableColumn.choices, default=TaxTableColumn.COL_1
    )
    start_date = models.DateField("Anställningsdatum", null=True, blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "personal_identity_number_hash"],
                name="uniq_employee_pnr_per_company",
            ),
        ]
        verbose_name = "Anställd"
        verbose_name_plural = "Anställda"

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        self.personal_identity_number_hash = blind_index(self.personal_identity_number)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "personal_identity_number" in update_fields:
            kwargs["update_fields"] = [*update_fields, "personal_identity_number_hash"]
        super().save(*args, **kwargs)

    @property
    def masked_personal_identity_number(self):
        """Personnummer med födelsedatum synligt och de fyra sista siffrorna dolda (GDPR G-004).

        Fullt värde får bara förekomma i AGI-XML och på lönebesked.
        """
        pnr = (self.personal_identity_number or "").replace("-", "").strip()
        if len(pnr) <= 4:
            return "XXXX"
        return f"{pnr[:-4]}-XXXX"

    def clean(self):
        super().clean()
        if not 1 <= (self.tax_table_number or 0) <= 40:
            raise ValidationError("Skattetabell måste vara mellan 1 och 40.")
        if not 1 <= (self.tax_table_column or 0) <= 6:
            raise ValidationError("Kolumn måste vara mellan 1 och 6.")


class PayrollRun(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="payroll_runs",
        verbose_name="Företag",
    )
    period_year = models.PositiveIntegerField("År")
    period_month = models.PositiveSmallIntegerField("Månad")
    payment_date = models.DateField("Utbetalningsdatum")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Skapad av",
    )
    is_finished = models.BooleanField("Avslutad", default=False)
    finished_at = models.DateTimeField("Avslutad tidpunkt", null=True, blank=True)
    finished_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finished_payroll_runs",
        verbose_name="Avslutad av",
    )
    booking_transaction = models.OneToOneField(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_run_booking",
        verbose_name="Bokföringsverifikation",
    )
    is_reported_to_skatteverket = models.BooleanField("Rapporterad till Skatteverket", default=False)
    reported_at = models.DateTimeField("Rapporterad tidpunkt", null=True, blank=True)
    paid_amount = models.DecimalField("Utbetalt nettobelopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_year", "-period_month", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "period_year", "period_month"],
                name="uniq_payroll_run_per_month",
            ),
        ]
        verbose_name = "Lönekörning"
        verbose_name_plural = "Lönekörningar"

    def __str__(self):
        return f"Lönekörning {self.period_year}-{self.period_month:02d}"

    SALARY_LIABILITY_ACCOUNT_NUMBER = "2910"

    def salary_payment_total(self):
        """Total net salary to pay out: the 2910 credit on the run's booking transaction."""
        from decimal import Decimal as _Decimal

        from django.db.models import Sum

        from bookkeeping.models import JournalEntry

        if not self.booking_transaction_id:
            return _Decimal("0.00")
        totals = JournalEntry.objects.filter(
            transaction_id=self.booking_transaction_id,
            account__number=self.SALARY_LIABILITY_ACCOUNT_NUMBER,
        ).aggregate(total_credit=Sum("credit"), total_debit=Sum("debit"))
        net = (totals["total_credit"] or _Decimal("0.00")) - (totals["total_debit"] or _Decimal("0.00"))
        return net.quantize(_Decimal("0.01"))

    def salary_payment_remaining(self):
        from decimal import Decimal as _Decimal

        remaining = self.salary_payment_total() - (self.paid_amount or _Decimal("0.00"))
        return remaining if remaining > _Decimal("0.00") else _Decimal("0.00")

    @property
    def is_fully_paid(self):
        total = self.salary_payment_total()
        return total > Decimal("0.00") and self.salary_payment_remaining() <= Decimal("0.00")

    @property
    def is_partially_paid(self):
        return (self.paid_amount or Decimal("0.00")) > Decimal("0.00") and not self.is_fully_paid

    @property
    def period_label(self):
        month_names = {
            1: "januari",
            2: "februari",
            3: "mars",
            4: "april",
            5: "maj",
            6: "juni",
            7: "juli",
            8: "augusti",
            9: "september",
            10: "oktober",
            11: "november",
            12: "december",
        }
        return f"{month_names.get(self.period_month, self.period_month)} {self.period_year}"


class PayrollReportEvidence(models.Model):
    payroll_run = models.OneToOneField(
        PayrollRun,
        on_delete=models.PROTECT,
        related_name="report_evidence",
        verbose_name="Lönekörning",
    )
    payload_json = models.TextField("AGI-underlag (JSON)")
    payload_hash = models.CharField("SHA256", max_length=64)
    generated_at = models.DateTimeField("Genererad", auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_payroll_report_evidence",
        verbose_name="Genererad av",
    )

    class Meta:
        ordering = ["-generated_at", "-id"]
        verbose_name = "AGI-bevispaket"
        verbose_name_plural = "AGI-bevispaket"

    def __str__(self):
        return f"AGI-bevis {self.payroll_run.period_year}-{self.payroll_run.period_month:02d}"


class SalaryRecord(models.Model):
    class TaxCalculationSource(models.TextChoices):
        API = "api", "Skatteverket API"
        TABLE_FALLBACK = "table_fallback", "Intern tabellberäkning (utfasad)"

    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name="salary_records",
        verbose_name="Lönekörning",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="salary_records",
        verbose_name="Anställd",
    )
    gross_salary = models.DecimalField("Bruttolön", max_digits=12, decimal_places=2)
    taxable_additions = models.DecimalField(
        "Skattepliktiga tillägg", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    non_taxable_additions = models.DecimalField(
        "Skattefria tillägg", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    pre_tax_deductions = models.DecimalField(
        "Avdrag före skatt", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    post_tax_deductions = models.DecimalField(
        "Avdrag efter skatt", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tax_table_number = models.PositiveSmallIntegerField("Skattetabell", default=32)
    tax_table_column = models.PositiveSmallIntegerField(
        "Kolumn", choices=TaxTableColumn.choices, default=TaxTableColumn.COL_1
    )
    preliminary_tax_rate = models.DecimalField(
        "Preliminärskatt (%)", max_digits=5, decimal_places=2, default=Decimal("0.00")
    )
    preliminary_tax_amount = models.DecimalField(
        "Skatteavdrag", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tax_calculation_source = models.CharField(
        "Skattekälla",
        max_length=20,
        choices=TaxCalculationSource.choices,
        default=TaxCalculationSource.API,
    )
    tax_calculation_reference = models.CharField("Skattereferens", max_length=120, blank=True)
    employer_contribution_rate = models.DecimalField(
        "Arbetsgivaravgift (%)", max_digits=5, decimal_places=2, default=Decimal("31.42")
    )
    employer_contribution_amount = models.DecimalField(
        "Arbetsgivaravgift", max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    net_salary = models.DecimalField("Nettolön", max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "employee"],
                name="uniq_salary_record_per_employee_and_run",
            ),
        ]
        verbose_name = "Lönepost"
        verbose_name_plural = "Löneposter"

    def __str__(self):
        return f"{self.employee} ({self.payroll_run.period_year}-{self.payroll_run.period_month:02d})"

    def clean(self):
        super().clean()
        if self.payroll_run_id and self.employee_id and self.payroll_run.company_id != self.employee.company_id:
            raise ValidationError("Anställd och lönekörning måste tillhöra samma företag.")
        if not 1 <= (self.tax_table_number or 0) <= 40:
            raise ValidationError("Skattetabell måste vara mellan 1 och 40.")
        if not 1 <= (self.tax_table_column or 0) <= 6:
            raise ValidationError("Kolumn måste vara mellan 1 och 6.")

    @property
    def taxable_salary(self):
        adjustment_totals = self.adjustment_totals()
        taxable_base = (
            (self.gross_salary or Decimal("0.00"))
            + (self.taxable_additions or Decimal("0.00"))
            + adjustment_totals["pre_add_taxable"]
            - (self.pre_tax_deductions or Decimal("0.00"))
            - adjustment_totals["pre_deduct"]
        )
        if taxable_base < Decimal("0.00"):
            taxable_base = Decimal("0.00")
        return quantize_money(taxable_base)

    def adjustment_totals(self):
        pre_add_taxable = Decimal("0.00")
        pre_add_non_taxable = Decimal("0.00")
        pre_deduct = Decimal("0.00")
        post_add = Decimal("0.00")
        post_deduct = Decimal("0.00")

        if self.pk is None:
            return {
                "pre_add_taxable": quantize_money(pre_add_taxable),
                "pre_add_non_taxable": quantize_money(pre_add_non_taxable),
                "pre_deduct": quantize_money(pre_deduct),
                "post_add": quantize_money(post_add),
                "post_deduct": quantize_money(post_deduct),
            }

        for adjustment in self.adjustments.all():
            amount = adjustment.amount or Decimal("0.00")
            if adjustment.phase == SalaryAdjustment.Phase.PRE_TAX:
                if adjustment.direction == SalaryAdjustment.Direction.ADDITION:
                    if adjustment.is_taxable:
                        pre_add_taxable += amount
                    else:
                        pre_add_non_taxable += amount
                else:
                    pre_deduct += amount
            else:
                if adjustment.direction == SalaryAdjustment.Direction.ADDITION:
                    post_add += amount
                else:
                    post_deduct += amount

        return {
            "pre_add_taxable": quantize_money(pre_add_taxable),
            "pre_add_non_taxable": quantize_money(pre_add_non_taxable),
            "pre_deduct": quantize_money(pre_deduct),
            "post_add": quantize_money(post_add),
            "post_deduct": quantize_money(post_deduct),
        }

    # Summeringsrader för lönespecen: gamla klumpfält + radvisa justeringar.
    @property
    def total_taxable_additions(self):
        return quantize_money((self.taxable_additions or Decimal("0.00")) + self.adjustment_totals()["pre_add_taxable"])

    @property
    def total_pre_tax_deductions(self):
        return quantize_money((self.pre_tax_deductions or Decimal("0.00")) + self.adjustment_totals()["pre_deduct"])

    @property
    def total_non_taxable_additions(self):
        totals = self.adjustment_totals()
        return quantize_money(
            (self.non_taxable_additions or Decimal("0.00")) + totals["pre_add_non_taxable"] + totals["post_add"]
        )

    @property
    def total_post_tax_deductions(self):
        return quantize_money((self.post_tax_deductions or Decimal("0.00")) + self.adjustment_totals()["post_deduct"])

    def calculate_amounts(self):
        taxable_salary = self.taxable_salary
        adjustment_totals = self.adjustment_totals()
        try:
            api_result = get_tax_amount_from_skatteverket(
                taxable_salary=taxable_salary,
                table_number=self.tax_table_number,
                table_column=self.tax_table_column,
                payment_date=self.payroll_run.payment_date,
                personal_identity_number=self.employee.personal_identity_number,
                company_org_number=self.payroll_run.company.org_number,
            )
        except Exception as exc:
            raise ValidationError(f"Kunde inte beräkna skatt via Skatteverkets API: {exc}")

        if api_result is None:
            raise ValidationError("Kunde inte beräkna skatt via Skatteverkets API.")

        tax_amount = api_result["tax_amount"]
        self.tax_calculation_source = self.TaxCalculationSource.API
        self.tax_calculation_reference = api_result.get("reference", "")

        employer_amount = taxable_salary * (self.employer_contribution_rate or Decimal("0.00")) / Decimal("100")
        self.preliminary_tax_amount = quantize_money(tax_amount)
        if taxable_salary > Decimal("0.00"):
            self.preliminary_tax_rate = quantize_money((self.preliminary_tax_amount / taxable_salary) * Decimal("100"))
        else:
            self.preliminary_tax_rate = Decimal("0.00")
        self.employer_contribution_amount = quantize_whole_kronor_down(employer_amount)
        self.net_salary = quantize_money(
            taxable_salary
            - self.preliminary_tax_amount
            + (self.non_taxable_additions or Decimal("0.00"))
            + adjustment_totals["pre_add_non_taxable"]
            + adjustment_totals["post_add"]
            - (self.post_tax_deductions or Decimal("0.00"))
            - adjustment_totals["post_deduct"]
        )
        if self.net_salary < Decimal("0.00"):
            raise ValidationError("Nettolönen kan inte bli negativ – avdragen överstiger lönen.")

    def save(self, *args, **kwargs):
        self.calculate_amounts()
        super().save(*args, **kwargs)


def _build_payroll_report_payload(payroll_run):
    record_rows = []
    for record in (
        payroll_run.salary_records.select_related("employee")
        .prefetch_related("adjustments")
        .order_by("employee__personal_identity_number_hash", "id")
    ):
        record_rows.append(
            {
                "employee": {
                    "personal_identity_number": record.employee.personal_identity_number,
                    "name": str(record.employee),
                },
                "gross_salary": str(record.gross_salary or Decimal("0.00")),
                "taxable_additions": str(record.taxable_additions or Decimal("0.00")),
                "non_taxable_additions": str(record.non_taxable_additions or Decimal("0.00")),
                "pre_tax_deductions": str(record.pre_tax_deductions or Decimal("0.00")),
                "post_tax_deductions": str(record.post_tax_deductions or Decimal("0.00")),
                "preliminary_tax_amount": str(record.preliminary_tax_amount or Decimal("0.00")),
                "employer_contribution_amount": str(record.employer_contribution_amount or Decimal("0.00")),
                "net_salary": str(record.net_salary or Decimal("0.00")),
                "tax_table_number": record.tax_table_number,
                "tax_table_column": record.tax_table_column,
                "tax_calculation_source": record.tax_calculation_source,
                "tax_calculation_reference": record.tax_calculation_reference,
                "adjustments": [
                    {
                        "phase": adj.phase,
                        "direction": adj.direction,
                        "description": adj.description,
                        "amount": str(adj.amount or Decimal("0.00")),
                        "is_taxable": bool(adj.is_taxable),
                    }
                    for adj in record.adjustments.all().order_by("id")
                ],
            }
        )

    totals = payroll_run.salary_records.aggregate(
        total_gross=models.Sum("gross_salary"),
        total_tax=models.Sum("preliminary_tax_amount"),
        total_employer=models.Sum("employer_contribution_amount"),
        total_net=models.Sum("net_salary"),
    )

    return {
        "schema": "agi-evidence-v1",
        "company": {
            "id": payroll_run.company_id,
            "name": payroll_run.company.name,
            "org_number": payroll_run.company.org_number,
        },
        "payroll_run": {
            "id": payroll_run.pk,
            "period_year": payroll_run.period_year,
            "period_month": payroll_run.period_month,
            "payment_date": payroll_run.payment_date.isoformat(),
            "finished_at": payroll_run.finished_at.isoformat() if payroll_run.finished_at else None,
            "booking_transaction_id": payroll_run.booking_transaction_id,
        },
        "totals": {
            "gross_salary": str(totals["total_gross"] or Decimal("0.00")),
            "preliminary_tax": str(totals["total_tax"] or Decimal("0.00")),
            "employer_contribution": str(totals["total_employer"] or Decimal("0.00")),
            "net_salary": str(totals["total_net"] or Decimal("0.00")),
        },
        "records": record_rows,
    }


def ensure_payroll_report_evidence(payroll_run, user=None):
    evidence = getattr(payroll_run, "report_evidence", None)
    if evidence is not None:
        return evidence

    payload = _build_payroll_report_payload(payroll_run)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    return PayrollReportEvidence.objects.create(
        payroll_run=payroll_run,
        payload_json=payload_json,
        payload_hash=payload_hash,
        generated_by=user,
    )


def mark_payroll_run_reported(payroll_run, user=None):
    evidence = ensure_payroll_report_evidence(payroll_run, user=user)
    if not payroll_run.is_reported_to_skatteverket:
        payroll_run.is_reported_to_skatteverket = True
        payroll_run.reported_at = timezone.now()
        payroll_run.save(update_fields=["is_reported_to_skatteverket", "reported_at"])
    return evidence


def mark_payroll_run_finished(payroll_run, user):
    from django.db import transaction as db_transaction

    from bookkeeping.models import Account, AccountingYear, JournalEntry, Transaction, TransactionSource
    from bookkeeping.period_locking import is_date_locked

    if payroll_run.is_finished:
        return payroll_run.booking_transaction

    if not payroll_run.salary_records.exists():
        raise ValidationError("Lönekörningen kan inte avslutas utan lönerader.")

    matching_years = AccountingYear.objects.filter(
        company=payroll_run.company,
        start_date__lte=payroll_run.payment_date,
        end_date__gte=payroll_run.payment_date,
    ).order_by("-start_date", "-id")
    if not matching_years.exists():
        raise ValidationError("Inget räkenskapsår matchar utbetalningsdatum för lönekörningen.")
    if matching_years.count() > 1:
        raise ValidationError("Flera räkenskapsår matchar utbetalningsdatum för lönekörningen.")
    if is_date_locked(payroll_run.company, payroll_run.payment_date):
        raise ValidationError(
            "Perioden för utbetalningsdatumet är låst. Bokför lönekörningen i öppen period eller lås upp perioden."
        )

    accounting_year = matching_years.first()
    account_numbers = {
        "salary_cost": "7010",
        "employer_cost": "7510",
        "tax_liability": "2710",
        "employer_liability": "2731",
        "salary_liability": "2910",
    }
    accounts = {
        key: Account.objects.filter(company=payroll_run.company, number=number, is_active=True).first()
        for key, number in account_numbers.items()
    }

    total_gross = Decimal("0.00")
    total_tax = Decimal("0.00")
    total_employer = Decimal("0.00")
    total_net = Decimal("0.00")
    total_generic_additions_on_salary_cost = Decimal("0.00")
    total_generic_deductions_on_salary_cost = Decimal("0.00")
    adjustment_account_totals = {}
    salary_records = list(payroll_run.salary_records.prefetch_related("adjustments"))
    for record in salary_records:
        # Recalculate right before posting so late adjustment edits are always reflected.
        record.save(
            update_fields=[
                "preliminary_tax_rate",
                "preliminary_tax_amount",
                "tax_calculation_source",
                "tax_calculation_reference",
                "employer_contribution_amount",
                "net_salary",
            ]
        )

        total_gross += record.gross_salary or Decimal("0.00")
        total_tax += record.preliminary_tax_amount or Decimal("0.00")
        total_employer += record.employer_contribution_amount or Decimal("0.00")
        total_net += record.net_salary or Decimal("0.00")

        total_generic_additions_on_salary_cost += (record.taxable_additions or Decimal("0.00")) + (
            record.non_taxable_additions or Decimal("0.00")
        )
        total_generic_deductions_on_salary_cost += (record.pre_tax_deductions or Decimal("0.00")) + (
            record.post_tax_deductions or Decimal("0.00")
        )

        for adjustment in record.adjustments.all():
            account_number = get_adjustment_account_number(adjustment)
            amount = adjustment.amount or Decimal("0.00")
            if account_number == "7010":
                if adjustment.direction == SalaryAdjustment.Direction.ADDITION:
                    total_generic_additions_on_salary_cost += amount
                else:
                    total_generic_deductions_on_salary_cost += amount
                continue

            if account_number not in adjustment_account_totals:
                adjustment_account_totals[account_number] = {"debit": Decimal("0.00"), "credit": Decimal("0.00")}
            if adjustment.direction == SalaryAdjustment.Direction.ADDITION:
                adjustment_account_totals[account_number]["debit"] += amount
            else:
                adjustment_account_totals[account_number]["credit"] += amount

    adjustment_account_numbers = sorted(adjustment_account_totals.keys())
    for account_number in adjustment_account_numbers:
        accounts[f"adjustment_{account_number}"] = Account.objects.filter(
            company=payroll_run.company,
            number=account_number,
            is_active=True,
        ).first()

    missing_accounts = [
        number
        for number, account in ((account_numbers.get(k, k.replace("adjustment_", "")), v) for k, v in accounts.items())
        if account is None
    ]
    if missing_accounts:
        raise ValidationError("Saknar konton för lönebokföring: " + ", ".join(sorted(missing_accounts)) + ".")

    total_additions_on_salary_cost = quantize_money(total_generic_additions_on_salary_cost)
    total_deductions_on_salary_cost = quantize_money(total_generic_deductions_on_salary_cost)
    base_salary_cost = quantize_money(total_gross)

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date=payroll_run.payment_date,
            description=f"Lönekörning {payroll_run.period_year}-{payroll_run.period_month:02d}",
            reference=f"LON-{payroll_run.period_year}{payroll_run.period_month:02d}",
            created_by=user,
            source=TransactionSource.PAYROLL,
        )

        JournalEntry.objects.create(
            transaction=txn,
            account=accounts["salary_cost"],
            debit=base_salary_cost,
            credit=Decimal("0.00"),
            description="Lönekostnad bruttolön",
        )
        if total_additions_on_salary_cost > Decimal("0.00"):
            JournalEntry.objects.create(
                transaction=txn,
                account=accounts["salary_cost"],
                debit=total_additions_on_salary_cost,
                credit=Decimal("0.00"),
                description="Lönekostnad tillägg",
            )
        if total_deductions_on_salary_cost > Decimal("0.00"):
            JournalEntry.objects.create(
                transaction=txn,
                account=accounts["salary_cost"],
                debit=Decimal("0.00"),
                credit=total_deductions_on_salary_cost,
                description="Lönekostnad avdrag",
            )
        for account_number in adjustment_account_numbers:
            totals = adjustment_account_totals[account_number]
            if totals["debit"] > Decimal("0.00"):
                JournalEntry.objects.create(
                    transaction=txn,
                    account=accounts[f"adjustment_{account_number}"],
                    debit=quantize_money(totals["debit"]),
                    credit=Decimal("0.00"),
                    description="Lönejustering tillägg",
                )
            if totals["credit"] > Decimal("0.00"):
                JournalEntry.objects.create(
                    transaction=txn,
                    account=accounts[f"adjustment_{account_number}"],
                    debit=Decimal("0.00"),
                    credit=quantize_money(totals["credit"]),
                    description="Lönejustering avdrag",
                )
        JournalEntry.objects.create(
            transaction=txn,
            account=accounts["employer_cost"],
            debit=quantize_money(total_employer),
            credit=Decimal("0.00"),
            description="Arbetsgivaravgifter",
        )
        JournalEntry.objects.create(
            transaction=txn,
            account=accounts["tax_liability"],
            debit=Decimal("0.00"),
            credit=quantize_money(total_tax),
            description="Personalskatt",
        )
        JournalEntry.objects.create(
            transaction=txn,
            account=accounts["employer_liability"],
            debit=Decimal("0.00"),
            credit=quantize_money(total_employer),
            description="Skuld arbetsgivaravgifter",
        )
        JournalEntry.objects.create(
            transaction=txn,
            account=accounts["salary_liability"],
            debit=Decimal("0.00"),
            credit=quantize_money(total_net),
            description="Skuld nettolöner",
        )

        txn.validate_balanced()

        SalaryPaymentReminder.objects.filter(payroll_run=payroll_run).delete()
        for record in salary_records:
            SalaryPaymentReminder.objects.create(
                company=payroll_run.company,
                payroll_run=payroll_run,
                employee=record.employee,
                due_date=payroll_run.payment_date,
                amount=quantize_money(record.net_salary or Decimal("0.00")),
                description=f"Utgående lön {record.employee} {payroll_run.period_year}-{payroll_run.period_month:02d}",
            )

        payroll_run.is_finished = True
        payroll_run.finished_at = timezone.now()
        payroll_run.finished_by = user
        payroll_run.booking_transaction = txn
        payroll_run.save(update_fields=["is_finished", "finished_at", "finished_by", "booking_transaction"])

        return txn


class SalaryAdjustment(models.Model):
    class Phase(models.TextChoices):
        PRE_TAX = "pre_tax", "Före skatt"
        POST_TAX = "post_tax", "Efter skatt"

    class Direction(models.TextChoices):
        ADDITION = "addition", "Tillägg"
        DEDUCTION = "deduction", "Avdrag"

    salary_record = models.ForeignKey(
        SalaryRecord,
        on_delete=models.CASCADE,
        related_name="adjustments",
        verbose_name="Lönepost",
    )
    phase = models.CharField("Tidpunkt", max_length=20, choices=Phase.choices, default=Phase.PRE_TAX)
    direction = models.CharField("Typ", max_length=20, choices=Direction.choices, default=Direction.ADDITION)
    category = models.CharField("Kategori", max_length=30, choices=AdjustmentCategory.choices, blank=True, default="")
    description = models.CharField("Beskrivning", max_length=200)
    amount = models.DecimalField("Belopp", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    is_taxable = models.BooleanField("Skattepliktig", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Lönejustering"
        verbose_name_plural = "Lönejusteringar"

    def __str__(self):
        return f"{self.get_direction_display()} {self.amount} ({self.get_phase_display()})"

    def clean(self):
        super().clean()
        if self.amount < Decimal("0.00"):
            raise ValidationError("Belopp kan inte vara negativt.")


class EmployeeDefaultAdjustment(models.Model):
    class Phase(models.TextChoices):
        PRE_TAX = "pre_tax", "Före skatt"
        POST_TAX = "post_tax", "Efter skatt"

    class Direction(models.TextChoices):
        ADDITION = "addition", "Tillägg"
        DEDUCTION = "deduction", "Avdrag"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="default_adjustments",
        verbose_name="Anställd",
    )
    phase = models.CharField("Tidpunkt", max_length=20, choices=Phase.choices, default=Phase.PRE_TAX)
    direction = models.CharField("Typ", max_length=20, choices=Direction.choices, default=Direction.ADDITION)
    category = models.CharField("Kategori", max_length=30, choices=AdjustmentCategory.choices, blank=True, default="")
    description = models.CharField("Beskrivning", max_length=200)
    amount = models.DecimalField("Belopp", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    is_taxable = models.BooleanField("Skattepliktig", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Standardjustering för anställd"
        verbose_name_plural = "Standardjusteringar för anställd"

    def __str__(self):
        return f"{self.employee} - {self.description}"

    def clean(self):
        super().clean()
        if self.amount < Decimal("0.00"):
            raise ValidationError("Belopp kan inte vara negativt.")


class SalaryPaymentReminder(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="salary_payment_reminders",
        verbose_name="Företag",
    )
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name="payment_reminders",
        verbose_name="Lönekörning",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="salary_payment_reminders",
        verbose_name="Anställd",
    )
    due_date = models.DateField("Förfallodatum")
    amount = models.DecimalField("Belopp", max_digits=12, decimal_places=2)
    description = models.CharField("Beskrivning", max_length=300)
    is_completed = models.BooleanField("Utförd", default=False)
    completed_at = models.DateTimeField("Utförd tidpunkt", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "employee"],
                name="uniq_salary_payment_reminder_per_employee_and_run",
            ),
        ]
        verbose_name = "Lönepåminnelse"
        verbose_name_plural = "Lönepåminnelser"

    def __str__(self):
        return f"{self.employee} {self.amount} {self.due_date}"
