import datetime
from decimal import Decimal

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db import transaction as db_transaction

from saldovibe.encryption import EncryptedTextField


def _as_date(value):
    """Tolerera ISO-strängar i DateFields på osparade instanser (jfr AccountingYear.ending_year)."""
    if value is None or isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


class AccountClass(models.TextChoices):
    ASSET = "1", "Klass 1 – Tillgångar"
    EQUITY_LIABILITY = "2", "Klass 2 – Eget kapital och skulder"
    REVENUE = "3", "Klass 3 – Rörelsens intäkter"
    COST_OF_GOODS = "4", "Klass 4 – Varuinköp och tillverkning"
    OTHER_EXTERNAL = "5", "Klass 5 – Övriga externa kostnader"
    OTHER_EXTERNAL_2 = "6", "Klass 6 – Övriga externa kostnader"
    PERSONNEL = "7", "Klass 7 – Anställda och personalkostnader"
    FINANCIAL = "8", "Klass 8 – Finansiella och andra poster"
    RESULTS_AND_TAX = "9", "Klass 9 - Årets resultat"
    YEAR_END = "10", "Klass 10 – Bokslutsdispositioner och skatter"


class Company(models.Model):
    class EmailProvider(models.TextChoices):
        NONE = "", "Ingen"
        GMAIL = "gmail", "Gmail"
        OUTLOOK = "outlook", "Outlook"

    class EmailSendProvider(models.TextChoices):
        NONE = "", "Ingen"
        SMTP = "smtp", "SMTP"
        GRAPH = "graph", "Microsoft 365 (Graph)"

    class EmailNotifyProvider(models.TextChoices):
        SYSTEM = "", "Systemkontot (standard)"
        OUTGOING = "outgoing", "Samma som utgående e-post"
        SMTP = "smtp", "Eget SMTP-konto"
        GRAPH = "graph", "Microsoft 365 (Graph), egen brevlåda"

    class VatReportingPeriod(models.TextChoices):
        NONE = "none", "Ingen momsredovisning"
        MONTHLY = "monthly", "Månadsvis"
        QUARTERLY = "quarterly", "Kvartalsvis"
        ANNUAL = "annual", "Årsvis"

    class LegalForm(models.TextChoices):
        AKTIEBOLAG = "aktiebolag", "Aktiebolag"
        ENSKILD_FIRMA = "enskild_firma", "Enskild firma"

    name = models.CharField("Företagsnamn", max_length=200, unique=True)
    org_number = models.CharField("Organisationsnummer", max_length=50, blank=True)
    # Blank för befintliga företag – bokslutsflödet vägrar starta tills fältet är satt
    # (inget gissande från org_number, se docs/compliance/aarsavslut/bokslutsflode-design.md).
    legal_form = models.CharField(
        "Bolagsform",
        max_length=20,
        choices=LegalForm.choices,
        blank=True,
        default="",
    )
    vat_number = models.CharField("Momsregistreringsnummer", max_length=50, blank=True)
    address = models.CharField("Adress", max_length=255, blank=True)
    postal_code = models.CharField("Postnummer", max_length=20, blank=True)
    city = models.CharField("Ort", max_length=100, blank=True)
    country_code = models.CharField("Landskod (ISO 3166-1 alpha-2)", max_length=2, default="SE", blank=True)
    phone_number = models.CharField("Telefon", max_length=50, blank=True)
    email = models.EmailField("E-post", blank=True)
    bankgiro = models.CharField("Bankgiro", max_length=50, blank=True)
    plusgiro = models.CharField("Plusgiro", max_length=50, blank=True)
    reminder_fee = models.DecimalField(
        "Påminnelseavgift",
        max_digits=10,
        decimal_places=2,
        default=Decimal("60.00"),
        help_text="Föreslagen avgift när en betalningspåminnelse skrivs ut.",
    )
    company_icon = models.FileField("Företagsikon", upload_to="company_icons/", blank=True)
    vat_reporting_period = models.CharField(
        "Momsredovisning",
        max_length=20,
        choices=VatReportingPeriod.choices,
        default=VatReportingPeriod.NONE,
        blank=True,
    )
    vat_start_date = models.DateField("Momsstartdatum", blank=True, null=True)
    email_fetch_enabled = models.BooleanField("Hämta e-postbilagor", default=False)
    email_fetch_provider = models.CharField(
        "E-postleverantör",
        max_length=20,
        choices=EmailProvider.choices,
        blank=True,
        default=EmailProvider.NONE,
    )
    email_fetch_address = models.EmailField("E-postkonto", blank=True)
    email_fetch_password = EncryptedTextField("App-lösenord", blank=True)
    email_fetch_oauth_tenant_id = models.CharField("Microsoft 365 Tenant ID", max_length=255, blank=True)
    email_fetch_oauth_client_id = models.CharField("Microsoft 365 Client ID", max_length=255, blank=True)
    email_fetch_oauth_client_secret = EncryptedTextField("Microsoft 365 Client Secret", blank=True)
    email_fetch_folder = models.CharField("Inkorgsmapp", max_length=100, blank=True, default="INBOX")
    email_fetch_last_error = models.TextField("Senaste importfel", blank=True, default="")
    email_fetch_last_error_at = models.DateTimeField("Senaste importfel vid", null=True, blank=True)
    email_send_provider = models.CharField(
        "Utgående e-post",
        max_length=20,
        choices=EmailSendProvider.choices,
        blank=True,
        default=EmailSendProvider.NONE,
    )
    # SMTP: avsändaradress. Graph: brevlådan det skickas från; tom = email_fetch_address.
    email_send_from = models.EmailField("Avsändaradress", blank=True)
    email_send_smtp_host = models.CharField("SMTP-server", max_length=255, blank=True)
    email_send_smtp_port = models.PositiveIntegerField("SMTP-port", default=587)
    email_send_smtp_username = models.CharField("SMTP-användarnamn", max_length=255, blank=True)
    email_send_smtp_password = EncryptedTextField("SMTP-lösenord", blank=True)
    email_send_smtp_use_tls = models.BooleanField("STARTTLS", default=True)
    email_notify_provider = models.CharField(
        "Notiskonto",
        max_length=20,
        choices=EmailNotifyProvider.choices,
        blank=True,
        default=EmailNotifyProvider.SYSTEM,
    )
    # SMTP: avsändaradress. Graph: brevlådan det skickas från; tom = email_fetch_address.
    email_notify_from = models.EmailField("Avsändaradress (notiser)", blank=True)
    email_notify_smtp_host = models.CharField("SMTP-server (notiser)", max_length=255, blank=True)
    email_notify_smtp_port = models.PositiveIntegerField("SMTP-port (notiser)", default=587)
    email_notify_smtp_username = models.CharField("SMTP-användarnamn (notiser)", max_length=255, blank=True)
    email_notify_smtp_password = EncryptedTextField("SMTP-lösenord (notiser)", blank=True)
    email_notify_smtp_use_tls = models.BooleanField("STARTTLS (notiser)", default=True)
    is_active = models.BooleanField("Aktiv", default=True)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="CompanyMembership",
        related_name="companies",
        verbose_name="Användare",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Företag"
        verbose_name_plural = "Företag"

    def __str__(self):
        return self.name


class CompanyMembership(models.Model):
    """Kopplar en användare till ett företag. Läsrollen (revisor) får se allt men
    spärras från varje POST i `company_scope.require_company`."""

    class Role(models.TextChoices):
        EDITOR = "editor", "Full behörighet"
        VIEWER = "viewer", "Endast läsa"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_column="customuser_id",
        related_name="company_memberships",
    )
    role = models.CharField("Roll", max_length=10, choices=Role.choices, default=Role.EDITOR)

    class Meta:
        db_table = "bookkeeping_company_users"
        unique_together = [("company", "user")]
        verbose_name = "Företagsanvändare"
        verbose_name_plural = "Företagsanvändare"

    def __str__(self):
        return f"{self.user} – {self.company} ({self.get_role_display()})"


class SentEmail(models.Model):
    """Sändlogg för utgående e-post. Ren driftlogg — bokför inget, auditkedjas inte."""

    class Purpose(models.TextChoices):
        INVOICE = "invoice", "Faktura"
        REMINDER = "reminder", "Betalningspåminnelse"
        DIGEST = "digest", "Daglig notissammanfattning"
        SALARY = "salary", "Lönespecifikation"

    class Status(models.TextChoices):
        SENT = "sent", "Skickad"
        FAILED = "failed", "Misslyckad"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="sent_emails", verbose_name="Företag")
    purpose = models.CharField("Typ", max_length=20, choices=Purpose.choices)
    recipient = models.EmailField("Mottagare")
    subject = models.CharField("Ämne", max_length=255)
    status = models.CharField("Status", max_length=10, choices=Status.choices)
    error = models.TextField("Felmeddelande", blank=True, default="")
    invoice = models.ForeignKey(
        "invoicing.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_emails",
        verbose_name="Faktura",
    )
    created_at = models.DateTimeField("Skickad", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="Skickad av",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Skickat e-postmeddelande"
        verbose_name_plural = "Skickade e-postmeddelanden"

    def __str__(self):
        return f"{self.get_purpose_display()} till {self.recipient} ({self.get_status_display()})"


class Account(models.Model):
    class VatFieldCode(models.TextChoices):
        NONE = "", "Ingen"
        FIELD_05 = "05", "05"
        FIELD_06 = "06", "06"
        FIELD_07 = "07", "07"
        FIELD_08 = "08", "08"
        FIELD_10 = "10", "10"
        FIELD_11 = "11", "11"
        FIELD_12 = "12", "12"
        FIELD_20 = "20", "20"
        FIELD_21 = "21", "21"
        FIELD_22 = "22", "22"
        FIELD_23 = "23", "23"
        FIELD_24 = "24", "24"
        FIELD_30 = "30", "30"
        FIELD_31 = "31", "31"
        FIELD_32 = "32", "32"
        FIELD_35 = "35", "35"
        FIELD_36 = "36", "36"
        FIELD_37 = "37", "37"
        FIELD_38 = "38", "38"
        FIELD_39 = "39", "39"
        FIELD_48 = "48", "48"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="accounts",
        verbose_name="Företag",
    )
    number = models.CharField("Kontonummer", max_length=10)
    name = models.CharField("Kontonamn", max_length=200)
    account_class = models.CharField("Kontoklass", max_length=2, choices=AccountClass.choices)
    vat_field_code = models.CharField(
        "Momsfält (Skatteverket)",
        max_length=2,
        choices=VatFieldCode.choices,
        blank=True,
        default=VatFieldCode.NONE,
    )
    sru_code = models.CharField(
        "SRU-kod",
        max_length=10,
        blank=True,
        default="",
        help_text="SRU-kod för Skatteverkets standardiserade räkenskapsutdrag (t.ex. 7011)",
    )
    description = models.TextField("Beskrivning", blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    include_in_liquidity_forecast = models.BooleanField(
        "Inkludera i likviditetsprognos",
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Styr om kontots saldo räknas med i dashboardens likviditetsprognos. "
            "Lämnas det tomt används standardvalet: bankkonton (193x) med ett saldo skilt från noll."
        ),
    )

    class Meta:
        ordering = ["number"]
        verbose_name = "Konto"
        verbose_name_plural = "Konton"
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_account_per_company"),
        ]

    def __str__(self):
        return f"{self.number} – {self.name}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old_number = Account.objects.filter(pk=self.pk).values_list("number", flat=True).first()
            if old_number is not None and old_number != self.number:
                from django.core.exceptions import ValidationError

                raise ValidationError("Kontonummer kan inte ändras efter att kontot har skapats.")
        if self._state.adding and not self.account_class:
            self.account_class = self._infer_account_class()
        if self._state.adding and not self.sru_code:
            from .sru_lookup import resolve_sru_code

            self.sru_code = resolve_sru_code(self.number, self.name)
        if self._state.adding and not self.vat_field_code:
            self.vat_field_code = self._infer_vat_field_code()
        super().save(*args, **kwargs)

    def _infer_account_class(self):
        first_digit = self.number[:1] if self.number else ""
        return first_digit if first_digit in "123456789" else "9"

    def _infer_vat_field_code(self):
        from .bas_accounts import lookup_bas_account

        bas_row = lookup_bas_account(self.number)
        if bas_row and bas_row.get("vat_field_code"):
            return bas_row["vat_field_code"]

        from vat.services import infer_vat_field_code

        return infer_vat_field_code(self.number)

    @classmethod
    def suggest_codes(cls, number, name=""):
        """Preview the account_class/sru_code/vat_field_code that save() would
        auto-fill for this number, without creating a database row. Used to show
        live suggestions in the account creation form before the user submits."""
        from .sru_lookup import resolve_sru_code

        probe = cls(number=(number or "").strip(), name=name or "")
        return {
            "account_class": probe._infer_account_class(),
            "sru_code": resolve_sru_code(probe.number, probe.name),
            "vat_field_code": probe._infer_vat_field_code(),
        }

    @property
    def is_balance_sheet(self):
        return self.account_class in ["1", "2"]

    @property
    def is_income_statement(self):
        return self.account_class in ["3", "4", "5", "6", "7", "8"]

    @property
    def normal_debit(self):
        """Assets and expenses/costs have a normal debit balance."""
        return self.account_class in ["1", "4", "5", "6", "7", "8", "9"]


class AccountingYear(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="accounting_years",
        verbose_name="Företag",
    )
    start_date = models.DateField("Startdatum")
    end_date = models.DateField("Slutdatum")

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Räkenskapsår"
        verbose_name_plural = "Räkenskapsår"
        constraints = [
            models.UniqueConstraint(fields=["company", "end_date"], name="uniq_year_per_company"),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_date} - {self.end_date})"

    @property
    def name(self):
        """Return display name derived from the ending year."""
        return str(self.ending_year) if self.ending_year is not None else ""

    @property
    def ending_year(self):
        """Return the ending year of the accounting year."""
        if not self.end_date:
            return None

        if hasattr(self.end_date, "year"):
            return self.end_date.year

        try:
            return datetime.date.fromisoformat(str(self.end_date)).year
        except ValueError:
            return None

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date is None or self.end_date is None:
            return

        if self.end_date < self.start_date:
            raise ValidationError("Slutdatum kan inte vara före startdatum.")

        if self.company_id:
            overlapping = AccountingYear.objects.filter(
                company_id=self.company_id,
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            ).exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError("Räkenskapsåret överlappar ett befintligt räkenskapsår.")


class PeriodLock(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="period_locks",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        AccountingYear,
        on_delete=models.CASCADE,
        related_name="period_locks",
        verbose_name="Räkenskapsår",
    )
    period_start = models.DateField("Period start")
    period_end = models.DateField("Period slut")
    is_locked = models.BooleanField("Låst", default=True)
    reason = models.CharField("Orsak (låsning)", max_length=255)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="period_locks_created",
        verbose_name="Låst av",
    )
    locked_at = models.DateTimeField("Låst vid", auto_now_add=True)
    reopened_reason = models.CharField("Orsak (upplåsning)", max_length=255, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="period_locks_reopened",
        verbose_name="Upplåst av",
    )
    reopened_at = models.DateTimeField("Upplåst vid", null=True, blank=True)

    class Meta:
        ordering = ["-period_start", "-id"]
        verbose_name = "Periodlås"
        verbose_name_plural = "Periodlås"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="period_lock_end_after_or_equal_start",
            ),
            models.UniqueConstraint(
                fields=["company", "accounting_year", "period_start", "period_end"],
                name="uniq_period_lock_range_per_year",
            ),
        ]

    def __str__(self):
        return f"{self.accounting_year.name}: {self.period_start} - {self.period_end}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.accounting_year_id and self.company_id and self.accounting_year.company_id != self.company_id:
            raise ValidationError("Räkenskapsåret tillhör ett annat företag.")
        if self.accounting_year_id and self.period_start and self.period_end:
            year_start = _as_date(self.accounting_year.start_date)
            year_end = _as_date(self.accounting_year.end_date)
            if _as_date(self.period_start) < year_start or _as_date(self.period_end) > year_end:
                raise ValidationError(f"Perioden måste ligga inom räkenskapsåret ({year_start} – {year_end}).")


# Classes 3-10 are the income-statement classes covered by build_income_statement_context
# (resultaträkning); 1-2 are the balance sheet (balansräkning) and have no budget concept here.
BUDGET_ACCOUNT_CLASSES = [
    AccountClass.REVENUE,
    AccountClass.COST_OF_GOODS,
    AccountClass.OTHER_EXTERNAL,
    AccountClass.OTHER_EXTERNAL_2,
    AccountClass.PERSONNEL,
    AccountClass.FINANCIAL,
    AccountClass.RESULTS_AND_TAX,
    AccountClass.YEAR_END,
]


class BudgetLine(models.Model):
    """One month's budgeted amount for one resultaträkning account, within one
    räkenskapsår. ``amount`` uses the same signed convention as the income statement's
    row amounts (credit-debit): positive for expected revenue, negative for expected cost -
    see build_income_statement_context in reports.py."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="budget_lines",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        AccountingYear,
        on_delete=models.CASCADE,
        related_name="budget_lines",
        verbose_name="Räkenskapsår",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="budget_lines",
        verbose_name="Konto",
    )
    month = models.PositiveSmallIntegerField("Månad")
    amount = models.DecimalField("Belopp", max_digits=15, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["accounting_year", "account__number", "month"]
        verbose_name = "Budgetrad"
        verbose_name_plural = "Budgetrader"
        constraints = [
            models.UniqueConstraint(
                fields=["accounting_year", "account", "month"],
                name="uniq_budget_line_per_year_account_month",
            ),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12), name="budget_line_month_1_12"),
        ]

    def __str__(self):
        return f"{self.account.number} {self.accounting_year.name}-{self.month:02d}: {self.amount}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.account_id and self.account.account_class in {AccountClass.ASSET, AccountClass.EQUITY_LIABILITY}:
            raise ValidationError("Budget kan bara sättas för resultaträkningens konton (klass 3-10).")
        if self.account_id and self.accounting_year_id and self.account.company_id != self.accounting_year.company_id:
            raise ValidationError("Kontot och räkenskapsåret måste tillhöra samma företag.")


class VerificationTemplate(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="verification_templates",
        verbose_name="Företag",
    )
    name = models.CharField("Mallnamn", max_length=200)
    description = models.CharField("Beskrivning", max_length=500, blank=True)
    slug = models.CharField("Katalogid", max_length=100, blank=True)
    source_url = models.URLField("Källa", blank=True)
    base_amount_label = models.CharField("Etikett för basbelopp", max_length=100, blank=True)
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Verifikationsmall"
        verbose_name_plural = "Verifikationsmallar"
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_verification_template_per_company"),
            models.UniqueConstraint(
                fields=["company", "slug"],
                condition=~models.Q(slug=""),
                name="uniq_verification_template_slug_per_company",
            ),
        ]

    def __str__(self):
        return self.name


class VerificationTemplateEntry(models.Model):
    class AmountRule(models.TextChoices):
        NONE = "none", "Fylls i manuellt"
        PERCENT = "percent", "Procent av basbeloppet"
        REMAINDER = "remainder", "Resterande belopp"

    template = models.ForeignKey(
        VerificationTemplate,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Mall",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        verbose_name="Konto",
    )
    is_debit = models.BooleanField("Debet", default=False)
    is_credit = models.BooleanField("Kredit", default=False)
    amount_rule = models.CharField(
        "Beloppsregel",
        max_length=20,
        choices=AmountRule.choices,
        default=AmountRule.NONE,
    )
    amount_percent = models.DecimalField(
        "Procent av basbelopp",
        max_digits=7,
        decimal_places=4,
        null=True,
        blank=True,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Mallrad"
        verbose_name_plural = "Mallrader"
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(is_debit=True) & models.Q(is_credit=False))
                    | (models.Q(is_debit=False) & models.Q(is_credit=True))
                ),
                name="verification_template_entry_exactly_one_side",
            ),
        ]

    def __str__(self):
        side = "Debet" if self.is_debit else "Kredit"
        return f"{self.template.name} – {self.account.number} ({side})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.is_debit == self.is_credit:
            raise ValidationError("Välj exakt en sida: debet eller kredit.")

        if self.amount_rule == self.AmountRule.PERCENT and self.amount_percent is None:
            raise ValidationError({"amount_percent": "Ange en procentsats när regeln är procent av basbeloppet."})
        if self.amount_rule != self.AmountRule.PERCENT and self.amount_percent is not None:
            raise ValidationError({"amount_percent": "Procentsats kan bara anges för regeln procent av basbeloppet."})


class VoucherSeries(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="voucher_series",
        verbose_name="Företag",
    )
    accounting_year = models.ForeignKey(
        AccountingYear,
        on_delete=models.CASCADE,
        related_name="voucher_series",
        verbose_name="Räkenskapsår",
    )
    code = models.CharField("Serie", max_length=10, default="A")
    next_number = models.PositiveIntegerField("Nästa verifikationsnummer", default=1)
    is_active = models.BooleanField("Aktiv", default=True)

    class Meta:
        ordering = ["code", "id"]
        verbose_name = "Verifikationsserie"
        verbose_name_plural = "Verifikationsserier"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "accounting_year", "code"],
                name="uniq_voucher_series_per_year",
            ),
        ]

    def __str__(self):
        return f"{self.code} ({self.accounting_year.name})"


class TransactionSource(models.TextChoices):
    MANUAL = "manual", "Manuell registrering"
    BANK = "bank", "Bank/kassa"
    SALES_INVOICE = "sales_invoice", "Kundfaktura"
    SUPPLIER_INVOICE = "supplier_invoice", "Leverantörsfaktura"
    EXPENSE = "expense", "Utlägg"
    PAYROLL = "payroll", "Lön"
    VAT = "vat", "Moms"
    FIXED_ASSET = "fixed_asset", "Anläggningstillgång"
    SIE_IMPORT = "sie_import", "Import (SIE/SI)"
    YEAR_END = "year_end", "Bokslut"


# Sensible starting point for a newly created company. Chosen to match common Swedish
# bookkeeping practice (separate series per journal/subledger) without mandating it —
# every code here is editable per company via VoucherSeriesRule.
DEFAULT_VOUCHER_SERIES_BY_SOURCE = {
    TransactionSource.MANUAL: "A",
    TransactionSource.BANK: "B",
    TransactionSource.SALES_INVOICE: "F",
    TransactionSource.SUPPLIER_INVOICE: "L",
    TransactionSource.EXPENSE: "U",
    TransactionSource.PAYROLL: "P",
    TransactionSource.VAT: "M",
    TransactionSource.FIXED_ASSET: "T",
    TransactionSource.SIE_IMPORT: "I",
    TransactionSource.YEAR_END: "S",
}


class VoucherSeriesRule(models.Model):
    """Company-level configuration: which voucher series a given transaction source posts into.

    Resolved automatically when a Transaction is created (see Transaction._assign_next_voucher_number);
    end users never pick a series manually. A finance_admin edits the mapping under Inställningar ->
    Verifikationsserier.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="voucher_series_rules",
        verbose_name="Företag",
    )
    source = models.CharField("Källa", max_length=20, choices=TransactionSource.choices)
    series_code = models.CharField("Serie", max_length=10)

    class Meta:
        ordering = ["source"]
        verbose_name = "Verifikationsserieregel"
        verbose_name_plural = "Verifikationsserieregler"
        constraints = [
            models.UniqueConstraint(fields=["company", "source"], name="uniq_voucher_series_rule_per_company_source"),
        ]

    def __str__(self):
        return f"{self.get_source_display()} → {self.series_code}"

    def clean(self):
        from django.core.exceptions import ValidationError

        code = (self.series_code or "").strip().upper()
        if not code:
            raise ValidationError("Serie måste anges.")
        if not code.isalnum():
            raise ValidationError("Serie får endast innehålla bokstäver och siffror.")

    def save(self, *args, **kwargs):
        self.series_code = (self.series_code or "").strip().upper()
        super().save(*args, **kwargs)

    @classmethod
    def resolve_series_code(cls, company, source):
        code = cls.objects.filter(company=company, source=source).values_list("series_code", flat=True).first()
        return code or DEFAULT_VOUCHER_SERIES_BY_SOURCE.get(source, "A")

    @classmethod
    def seed_defaults_for_company(cls, company):
        existing_sources = set(cls.objects.filter(company=company).values_list("source", flat=True))
        # Saved one by one (a handful of rows, once per company) so post_save
        # fires and the audit log records the seeding — bulk_create skips signals.
        for source, code in DEFAULT_VOUCHER_SERIES_BY_SOURCE.items():
            if source not in existing_sources:
                cls(company=company, source=source, series_code=code).save()


class Transaction(models.Model):
    accounting_year = models.ForeignKey(
        AccountingYear,
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name="Räkenskapsår",
    )
    date = models.DateField("Datum")
    description = models.CharField("Beskrivning", max_length=500)
    reference = models.CharField("Referens/Verifikationsnr", max_length=100, blank=True)
    source = models.CharField(
        "Källa", max_length=20, choices=TransactionSource.choices, default=TransactionSource.MANUAL
    )
    voucher_series = models.CharField("Verifikationsserie", max_length=10, blank=True)
    voucher_number = models.PositiveIntegerField("Verifikationsnummer", null=True, blank=True)
    correction_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
        verbose_name="Korrigering av",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Skapad av",
    )
    attachments = models.ManyToManyField(
        "attachments.TransactionAttachment",
        related_name="transactions",
        blank=True,
        verbose_name="Bilagor",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Verifikation"
        verbose_name_plural = "Verifikationer"
        constraints = [
            models.UniqueConstraint(
                fields=["accounting_year", "voucher_series", "voucher_number"],
                name="uniq_voucher_number_per_year_series",
            ),
        ]

    def __str__(self):
        return f"{self.date} – {self.description}"

    @property
    def total_debit(self):
        return sum(e.debit for e in self.entries.all()) or Decimal("0.00")

    @property
    def total_credit(self):
        return sum(e.credit for e in self.entries.all()) or Decimal("0.00")

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    @property
    def is_reversed(self):
        return self.correction_of_id is not None or self.corrections.exists()

    def validate_balanced(self):
        from django.core.exceptions import ValidationError

        if not self.is_balanced:
            raise ValidationError(
                f"Verifikationen är inte i balans. Debet: {self.total_debit} – Kredit: {self.total_credit}"
            )

    def _resolve_voucher_series_code(self):
        # A correction/reversal always stays in the series of the voucher it corrects,
        # regardless of which source-based series would otherwise apply.
        if self.correction_of_id and self.correction_of.voucher_series:
            return self.correction_of.voucher_series
        return VoucherSeriesRule.resolve_series_code(self.accounting_year.company, self.source)

    def _assign_next_voucher_number(self):
        if not self.accounting_year_id:
            return

        series_code = self._resolve_voucher_series_code()
        series, _ = VoucherSeries.objects.select_for_update().get_or_create(
            company=self.accounting_year.company,
            accounting_year=self.accounting_year,
            code=series_code,
            defaults={"next_number": 1, "is_active": True},
        )

        self.voucher_series = series_code
        self.voucher_number = series.next_number
        series.next_number += 1
        series.save(update_fields=["next_number"])

        if not (self.reference or "").strip():
            self.reference = f"{self.voucher_series}{self.voucher_number}"

    def save(self, *args, **kwargs):
        with db_transaction.atomic():
            if self._state.adding:
                # Safety net under the per-service checks: no posting path may
                # create a voucher in a locked period, even one that forgot its
                # own is_date_locked call. Updates/deletes are blocked by the
                # DB triggers in migration 0036.
                self._validate_period_open()
                if self.voucher_number is None:
                    self._assign_next_voucher_number()
            super().save(*args, **kwargs)

    def _validate_period_open(self):
        from django.core.exceptions import ValidationError

        from .period_locking import is_date_locked

        if not self.accounting_year_id:
            return

        year = self.accounting_year
        year_start, year_end = _as_date(year.start_date), _as_date(year.end_date)
        if not (year_start <= _as_date(self.date) <= year_end):
            raise ValidationError(f"Verifikationsdatumet ligger utanför räkenskapsåret ({year_start} – {year_end}).")
        if is_date_locked(year.company, self.date):
            raise ValidationError(
                "Perioden för verifikationsdatumet är låst. Bokför i öppen period eller lås upp perioden."
            )


class JournalEntry(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name="Konto")
    debit = models.DecimalField("Debet", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    credit = models.DecimalField("Kredit", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    description = models.CharField("Beskrivning", max_length=300, blank=True)

    class Meta:
        verbose_name = "Konteringsrad"
        verbose_name_plural = "Konteringsrader"
        constraints = [
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="journal_entry_not_both_debit_and_credit",
            ),
            # En negativ debet är en dold kredit (och tvärtom) — den skulle passera
            # validate_balanced men korrumpera rapporter och SIE-export.
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0),
                name="journal_entry_amounts_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.account} D:{self.debit} K:{self.credit}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if (self.debit or Decimal("0.00")) > Decimal("0.00") and (self.credit or Decimal("0.00")) > Decimal("0.00"):
            raise ValidationError("En konteringsrad kan inte ha belopp i både debet och kredit.")
        if (self.debit or Decimal("0.00")) < Decimal("0.00") or (self.credit or Decimal("0.00")) < Decimal("0.00"):
            raise ValidationError("Debet och kredit kan inte vara negativa.")

    def save(self, *args, **kwargs):
        # Cross-tenant-skydd: varje posting-service skopar sina querysets per företag,
        # men en enda miss skulle blanda två företags huvudböcker. Raderna är
        # append-only (DB-triggrar blockerar update), så checken behövs bara här.
        if self._state.adding and self.account_id and self.transaction_id:
            if self.account.company_id != self.transaction.accounting_year.company_id:
                from django.core.exceptions import ValidationError

                raise ValidationError("Kontot tillhör ett annat företag än verifikationen.")
        super().save(*args, **kwargs)


# Deliberately not under MEDIA_ROOT: nginx serves /media/ completely unauthenticated
# (nginx/default.conf), but an export bundle is a company's entire bookkeeping data in one
# file. Storing it under its own DATA_DIR subfolder means it's only ever reachable through
# the authenticated download view, never by guessing a /media/ URL.
#
# A callable, not a plain instance: FileField(storage=...) deconstructs a Storage instance's
# constructor args verbatim into the migration file, which would freeze this environment's
# resolved DATA_DIR path (e.g. a dev machine's repo checkout path) into the migration forever.
# A callable is instead referenced by its dotted path and re-invoked per environment.
def export_bundle_storage():
    return FileSystemStorage(location=str(settings.DATA_DIR / "export_bundles"))


class ExportJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Väntar"
        RUNNING = "running", "Pågår"
        COMPLETED = "completed", "Klar"
        FAILED = "failed", "Misslyckades"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="export_jobs", verbose_name="Företag")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Startad av",
    )
    start_date = models.DateField("Från och med")
    end_date = models.DateField("Till och med")
    status = models.CharField("Status", max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField("Felmeddelande", blank=True)
    file = models.FileField("Fil", storage=export_bundle_storage, upload_to="%Y/%m/", blank=True)
    created_at = models.DateTimeField("Skapad", auto_now_add=True)
    started_at = models.DateTimeField("Startad", null=True, blank=True)
    completed_at = models.DateTimeField("Klar", null=True, blank=True)
    seen_at = models.DateTimeField("Sedd", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Exportpaket"
        verbose_name_plural = "Exportpaket"

    def __str__(self):
        return f"Export {self.company} {self.start_date}–{self.end_date} ({self.status})"
