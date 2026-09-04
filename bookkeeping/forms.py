from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory, modelformset_factory
from django.utils import timezone

from .form_utils import normalize_decimal_fields
from .models import (
    BUDGET_ACCOUNT_CLASSES,
    Account,
    AccountClass,
    AccountingYear,
    BudgetLine,
    Company,
    CompanyMembership,
    JournalEntry,
    PeriodLock,
    Transaction,
    VerificationTemplate,
    VerificationTemplateEntry,
    VoucherSeriesRule,
)

MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "Maj",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dec",
)


def budget_field_name(account_id, month):
    return f"amount_{account_id}_{month}"


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ("date", "description")
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date", "autofocus": "autofocus"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
        }
        labels = {
            "date": "Datum",
            "description": "Beskrivning",
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if self.instance.pk is None and not self.is_bound and self.fields["date"].initial is None:
            self.fields["date"].initial = timezone.localdate()


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ("account", "debit", "credit")
        widgets = {
            "account": forms.Select(attrs={"class": "form-select"}),
            "debit": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
            "credit": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
        }
        labels = {
            "account": "Konto",
            "debit": "Debet",
            "credit": "Kredit",
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        queryset = Account.objects.filter(is_active=True).order_by("number")
        if company is not None:
            queryset = queryset.filter(company=company)
        self.fields["account"].queryset = queryset
        self.fields["account"].empty_label = "Välj konto…"

    def clean(self):
        cleaned_data = super().clean()
        debit = cleaned_data.get("debit") or 0
        credit = cleaned_data.get("credit") or 0

        if debit > 0 and credit > 0:
            raise forms.ValidationError("En konteringsrad kan inte ha belopp i både debet och kredit.")

        return cleaned_data


class JournalEntryInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        entries = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        total_debit = sum((form.cleaned_data.get("debit") or Decimal("0.00")) for form in entries)
        total_credit = sum((form.cleaned_data.get("credit") or Decimal("0.00")) for form in entries)

        if total_debit != total_credit:
            raise forms.ValidationError(
                f"Verifikationen är inte i balans. Debet: {total_debit} – Kredit: {total_credit}"
            )


JournalEntryFormSet = inlineformset_factory(
    Transaction,
    JournalEntry,
    form=JournalEntryForm,
    formset=JournalEntryInlineFormSet,
    extra=0,
    can_delete=True,
    min_num=2,
    validate_min=True,
)


class AccountingYearForm(forms.ModelForm):
    @staticmethod
    def _default_end_date(start_date):
        """Return one year minus one day from start_date."""
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)

        try:
            next_year_same_day = start_date.replace(year=start_date.year + 1)
        except ValueError:
            # Handle leap-day starts by using March 1 the next year, then subtracting one day.
            next_year_same_day = date(start_date.year + 1, 3, 1)

        return next_year_same_day - timedelta(days=1)

    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        # For new accounting years, suggest a start date directly after the latest year ends.
        if self.instance.pk is None and self.company is not None:
            previous_year = AccountingYear.objects.filter(company=self.company).order_by("-end_date", "-id").first()
            if previous_year is not None:
                self.fields["start_date"].initial = previous_year.end_date + timedelta(days=1)
                # Keep continuity by locking start date for non-first accounting years.
                self.fields["start_date"].disabled = True

        if self.instance.pk is None and not self.is_bound and self.fields["end_date"].initial is None:
            start_initial = self.fields["start_date"].initial
            if start_initial is not None:
                self.fields["end_date"].initial = self._default_end_date(start_initial)

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")

        if self.instance.pk is None and self.company is not None and start_date is not None:
            previous_year = AccountingYear.objects.filter(company=self.company).order_by("-end_date", "-id").first()
            if previous_year is not None:
                expected_start = previous_year.end_date + timedelta(days=1)
                if start_date != expected_start:
                    self.add_error(
                        "start_date",
                        f"Startdatum måste vara dagen efter föregående räkenskapsårs slutdatum ({expected_start:%Y-%m-%d}).",
                    )

        return cleaned_data

    class Meta:
        model = AccountingYear
        fields = ("start_date", "end_date")
        widgets = {
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }


class PeriodLockForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.accounting_year = kwargs.pop("accounting_year")
        super().__init__(*args, **kwargs)
        self.fields["period_start"].initial = self.accounting_year.start_date
        self.fields["period_end"].initial = self.accounting_year.end_date

    def clean(self):
        cleaned_data = super().clean()
        period_start = cleaned_data.get("period_start")
        period_end = cleaned_data.get("period_end")

        if period_start is None or period_end is None:
            return cleaned_data

        if period_end < period_start:
            self.add_error("period_end", "Slutdatum kan inte vara före startdatum.")
            return cleaned_data

        if period_start < self.accounting_year.start_date or period_end > self.accounting_year.end_date:
            self.add_error(
                None,
                f"Perioden måste ligga inom räkenskapsåret ({self.accounting_year.start_date} – {self.accounting_year.end_date}).",
            )
            return cleaned_data

        overlapping = PeriodLock.objects.filter(
            accounting_year=self.accounting_year,
            period_start__lte=period_end,
            period_end__gte=period_start,
        )
        if overlapping.exists():
            self.add_error(
                None,
                "Perioden överlappar en redan registrerad periodlåsning. "
                "Lås upp eller lås om den befintliga posten istället för att skapa en ny.",
            )

        return cleaned_data

    class Meta:
        model = PeriodLock
        fields = ("period_start", "period_end", "reason")
        widgets = {
            "period_start": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "period_end": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "reason": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "t.ex. Bokslut 2026 eller Månadsavstämning januari"}
            ),
        }


class PeriodLockReopenForm(forms.Form):
    reopened_reason = forms.CharField(
        label="Orsak (upplåsning)",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ange orsak till upplåsning"}),
    )


class PeriodLockRelockForm(forms.Form):
    reason = forms.CharField(
        label="Orsak (ny låsning)",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ange orsak till förnyad låsning"}),
    )


class AccountForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Account numbers are immutable after creation.
            self.fields["number"].disabled = True
            self.fields["number"].help_text = "Kontonummer kan inte ändras efter att kontot har skapats."
        else:
            self.fields[
                "number"
            ].help_text = (
                "4 siffror enligt BAS-kontoplanen, t.ex. 1930. Kontoklass sätts automatiskt utifrån kontonumret."
            )

    def clean_number(self):
        number = self.cleaned_data["number"].strip()
        if not number.isdigit() or len(number) != 4:
            raise forms.ValidationError("Kontonummer måste bestå av exakt 4 siffror, enligt BAS-kontoplanen.")
        if self.instance.pk is None and self.company is not None:
            if Account.objects.filter(company=self.company, number=number).exists():
                raise forms.ValidationError(f"Konto {number} finns redan för företaget.")
        return number

    class Meta:
        model = Account
        fields = ("number", "name", "vat_field_code", "sru_code", "description", "is_active")
        widgets = {
            "number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "inputmode": "numeric",
                    "pattern": "[0-9]*",
                    "maxlength": "4",
                    "oninput": "this.value = this.value.replace(/[^0-9]/g, '').slice(0, 4);",
                }
            ),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "vat_field_code": forms.Select(attrs={"class": "form-select"}),
            "sru_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "t.ex. 7011"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class CompanyForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ett förslagsvärde ska inte kunna blockera en företagsuppdatering:
        # lämnas fältet tomt behålls nuvarande värde (eller standardvärdet).
        self.fields["reminder_fee"].required = False
        self.fields["email_send_smtp_port"].required = False
        self.fields["email_notify_smtp_port"].required = False

    def clean_reminder_fee(self):
        value = self.cleaned_data.get("reminder_fee")
        if value is not None:
            return value
        if self.instance.pk:
            return self.instance.reminder_fee
        return Company._meta.get_field("reminder_fee").default

    def clean_email_send_smtp_port(self):
        value = self.cleaned_data.get("email_send_smtp_port")
        if value is not None:
            return value
        if self.instance.pk:
            return self.instance.email_send_smtp_port
        return Company._meta.get_field("email_send_smtp_port").default

    def clean_email_notify_smtp_port(self):
        value = self.cleaned_data.get("email_notify_smtp_port")
        if value is not None:
            return value
        if self.instance.pk:
            return self.instance.email_notify_smtp_port
        return Company._meta.get_field("email_notify_smtp_port").default

    class Meta:
        model = Company
        fields = (
            "name",
            "org_number",
            "legal_form",
            "vat_number",
            "country_code",
            "company_icon",
            "address",
            "postal_code",
            "city",
            "phone_number",
            "email",
            "bankgiro",
            "plusgiro",
            "reminder_fee",
            "vat_reporting_period",
            "vat_start_date",
            "email_fetch_enabled",
            "email_fetch_provider",
            "email_fetch_address",
            "email_fetch_password",
            "email_fetch_oauth_tenant_id",
            "email_fetch_oauth_client_id",
            "email_fetch_oauth_client_secret",
            "email_fetch_folder",
            "email_send_provider",
            "email_send_from",
            "email_send_smtp_host",
            "email_send_smtp_port",
            "email_send_smtp_username",
            "email_send_smtp_password",
            "email_send_smtp_use_tls",
            "email_notify_provider",
            "email_notify_from",
            "email_notify_smtp_host",
            "email_notify_smtp_port",
            "email_notify_smtp_username",
            "email_notify_smtp_password",
            "email_notify_smtp_use_tls",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "org_number": forms.TextInput(attrs={"class": "form-control"}),
            "legal_form": forms.Select(attrs={"class": "form-select"}),
            "vat_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "SE123456789001"}),
            "country_code": forms.TextInput(
                attrs={"class": "form-control text-uppercase", "maxlength": "2", "placeholder": "SE"}
            ),
            "company_icon": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "bankgiro": forms.TextInput(attrs={"class": "form-control"}),
            "plusgiro": forms.TextInput(attrs={"class": "form-control"}),
            "reminder_fee": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "vat_reporting_period": forms.Select(attrs={"class": "form-select"}),
            "vat_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "email_fetch_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "email_fetch_provider": forms.Select(attrs={"class": "form-select"}),
            "email_fetch_address": forms.EmailInput(attrs={"class": "form-control"}),
            "email_fetch_password": forms.PasswordInput(attrs={"class": "form-control"}, render_value=False),
            "email_fetch_oauth_tenant_id": forms.TextInput(attrs={"class": "form-control"}),
            "email_fetch_oauth_client_id": forms.TextInput(attrs={"class": "form-control"}),
            "email_fetch_oauth_client_secret": forms.PasswordInput(attrs={"class": "form-control"}, render_value=False),
            "email_fetch_folder": forms.TextInput(attrs={"class": "form-control"}),
            "email_send_provider": forms.Select(attrs={"class": "form-select"}),
            "email_send_from": forms.EmailInput(attrs={"class": "form-control"}),
            "email_send_smtp_host": forms.TextInput(attrs={"class": "form-control"}),
            "email_send_smtp_port": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "65535"}),
            "email_send_smtp_username": forms.TextInput(attrs={"class": "form-control"}),
            "email_send_smtp_password": forms.PasswordInput(attrs={"class": "form-control"}, render_value=False),
            "email_send_smtp_use_tls": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "email_notify_provider": forms.Select(attrs={"class": "form-select"}),
            "email_notify_from": forms.EmailInput(attrs={"class": "form-control"}),
            "email_notify_smtp_host": forms.TextInput(attrs={"class": "form-control"}),
            "email_notify_smtp_port": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "65535"}),
            "email_notify_smtp_username": forms.TextInput(attrs={"class": "form-control"}),
            "email_notify_smtp_password": forms.PasswordInput(attrs={"class": "form-control"}, render_value=False),
            "email_notify_smtp_use_tls": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class SIEImportForm(forms.Form):
    sie_file = forms.FileField(
        label="SIE-fil",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".se,.sie,.txt"}),
    )
    confirm_replace = forms.BooleanField(
        required=False,
        initial=False,
        label="Jag bekräftar att tidigare importerade verifikationer för räkenskapsåret ska tas bort och ersättas av filens innehåll.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        kwargs.pop("company", None)
        super().__init__(*args, **kwargs)


class SIImportForm(forms.Form):
    si_file = forms.FileField(
        label="SI-fil",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".si,.sie,.se,.txt",
                "hidden": True,
                "onchange": "if(this.files.length)this.form.requestSubmit()",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        kwargs.pop("company", None)
        super().__init__(*args, **kwargs)


class VerificationTemplateForm(forms.ModelForm):
    class Meta:
        model = VerificationTemplate
        fields = ("name", "description", "base_amount_label", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autofocus": "autofocus"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "base_amount_label": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "t.ex. Bruttobelopp inkl. moms"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "base_amount_label": "Visas som etikett på basbeloppsfältet när mallen används.",
        }


class VerificationTemplateEntryForm(forms.ModelForm):
    class Meta:
        model = VerificationTemplateEntry
        fields = ("account", "is_debit", "is_credit", "amount_rule", "amount_percent")
        widgets = {
            "account": forms.Select(attrs={"class": "form-select"}),
            "is_debit": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_credit": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "amount_rule": forms.Select(attrs={"class": "form-select form-select-sm entry-amount-rule"}),
            "amount_percent": forms.TextInput(
                attrs={"class": "form-control form-control-sm entry-amount-percent", "inputmode": "decimal"}
            ),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        queryset = Account.objects.filter(is_active=True).order_by("number")
        if company is not None:
            queryset = queryset.filter(company=company)
        self.fields["account"].queryset = queryset
        self.fields["account"].empty_label = "Välj konto…"

    def clean(self):
        cleaned_data = super().clean()
        is_debit = bool(cleaned_data.get("is_debit"))
        is_credit = bool(cleaned_data.get("is_credit"))

        if is_debit == is_credit:
            raise forms.ValidationError("Välj exakt en sida: debet eller kredit.")

        rule = cleaned_data.get("amount_rule")
        percent = cleaned_data.get("amount_percent")

        if rule == VerificationTemplateEntry.AmountRule.PERCENT:
            if percent is None:
                self.add_error("amount_percent", "Ange en procentsats.")
            elif percent <= 0:
                self.add_error("amount_percent", "Procentsatsen måste vara positiv.")
        elif percent is not None:
            cleaned_data["amount_percent"] = None

        return cleaned_data


class VerificationTemplateEntryInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        entries = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]

        if len(entries) < 2:
            raise forms.ValidationError("Mallen måste innehålla minst två rader.")

        remainders = sum(
            1
            for form in entries
            if form.cleaned_data.get("amount_rule") == VerificationTemplateEntry.AmountRule.REMAINDER
        )
        if remainders > 1:
            raise forms.ValidationError("Bara en rad kan ha regeln 'Resterande belopp'.")


VerificationTemplateEntryFormSet = inlineformset_factory(
    VerificationTemplate,
    VerificationTemplateEntry,
    form=VerificationTemplateEntryForm,
    formset=VerificationTemplateEntryInlineFormSet,
    extra=0,
    can_delete=True,
    min_num=2,
    validate_min=True,
)


class VoucherSeriesRuleForm(forms.ModelForm):
    class Meta:
        model = VoucherSeriesRule
        fields = ("series_code",)
        widgets = {
            "series_code": forms.TextInput(
                attrs={"class": "form-control form-control-sm text-uppercase", "maxlength": "10"}
            ),
        }


VoucherSeriesRuleFormSet = modelformset_factory(
    VoucherSeriesRule,
    form=VoucherSeriesRuleForm,
    extra=0,
    can_delete=False,
)


class LiquidityForecastAccountForm(forms.ModelForm):
    include_in_liquidity_forecast = forms.BooleanField(
        label="Inkludera i likviditetsprognos",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Account
        fields = ("include_in_liquidity_forecast",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.pk and instance.include_in_liquidity_forecast is None:
            balance = (getattr(instance, "liquidity_debit", None) or Decimal("0")) - (
                getattr(instance, "liquidity_credit", None) or Decimal("0")
            )
            self.initial["include_in_liquidity_forecast"] = instance.number.startswith("193") and balance != Decimal(
                "0"
            )


LiquidityForecastAccountFormSet = modelformset_factory(
    Account,
    form=LiquidityForecastAccountForm,
    extra=0,
    can_delete=False,
)


class AccountChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, account):
        return f"{account.number} {account.name}"


class RegisterPaymentForm(forms.Form):
    """Register a manual payment and/or write-off against a payable, posting a verifikation.

    Shared by customer invoices, supplier invoices and expense claims. The payable decides
    the suggested write-off account (öresavrundning 3740, kundförlust 6351) and whether the
    VAT-adjustment choice is offered (customer invoices only).
    """

    payment_date = forms.DateField(
        label="Betalningsdatum",
        widget=forms.DateInput(attrs={"class": "form-control form-control-sm", "type": "date"}),
    )
    amount = forms.DecimalField(
        label="Betalt belopp",
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
    )
    payment_account = AccountChoiceField(
        label="Betalkonto",
        queryset=Account.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm js-account-select"}),
    )
    write_off_amount = forms.DecimalField(
        label="Avskrivet belopp",
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
    )
    write_off_account = AccountChoiceField(
        label="Avskrivningskonto",
        queryset=Account.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm js-account-select"}),
    )
    adjust_vat = forms.BooleanField(
        label="Justera utgående moms (kundförlust)",
        required=False,
        initial=True,
        help_text="Delar avskrivningen i kundförlust och återtagen moms per momssats.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, payable, **kwargs):
        super().__init__(*args, **kwargs)
        self.payable = payable
        accounts = payable.company.accounts.filter(is_active=True).order_by("number")
        # Betalkonto: kassa/bank (19xx) samt ägarens privata betalning — 2018 (egna
        # insättningar, EF) och 2893 (avräkning aktieägare, AB). Inte t.ex. kundfordringar.
        payment_accounts = accounts.filter(Q(number__startswith="19") | Q(number__in=["2018", "2893"]))
        # Avskrivningskonto: inte balanskonton (reskontra, kassa/bank) som redan täcks av
        # betalningen/regleringen — bara resultatkonton (öresavrundning, kundförlust, rabatt ...).
        write_off_accounts = accounts.exclude(account_class__in=[AccountClass.ASSET, AccountClass.EQUITY_LIABILITY])
        self.fields["payment_account"].queryset = payment_accounts
        self.fields["write_off_account"].queryset = write_off_accounts

        if not self.is_bound:
            self.fields["payment_date"].initial = timezone.localdate()
            self.fields["amount"].initial = payable.remaining_amount
            self.fields["payment_account"].initial = payment_accounts.filter(number="1930").first()
            self.fields["write_off_account"].initial = write_off_accounts.filter(
                number=payable.PAYMENT_WRITE_OFF_DEFAULT_ACCOUNT
            ).first()
        if not payable.PAYMENT_VAT_ADJUST:
            del self.fields["adjust_vat"]

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount") or Decimal("0.00")
        write_off = cleaned.get("write_off_amount") or Decimal("0.00")
        if amount + write_off <= Decimal("0.00"):
            raise forms.ValidationError("Ange ett betalbelopp och/eller ett avskrivningsbelopp.")
        if amount > Decimal("0.00") and cleaned.get("payment_account") is None:
            raise forms.ValidationError("Välj ett betalkonto för betalbeloppet.")
        if write_off > Decimal("0.00") and cleaned.get("write_off_account") is None:
            raise forms.ValidationError("Välj ett konto för avskrivningsbeloppet.")
        return cleaned


class ExportBundleForm(forms.Form):
    """Free date range for the bokföringsexport bundle (SIE/kontospecifikationer/bilagor)."""

    start_date = forms.DateField(
        label="Från och med",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    end_date = forms.DateField(
        label="Till och med",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Slutdatum måste vara samma som eller efter startdatum.")
        return cleaned_data


class PeriodizationForm(forms.Form):
    """Belopp, konto, motkonto (17xx/29xx), antal månader, startmånad -> N verifikationer.

    ``account`` is the P&L (resultaträkning) account the cost/revenue belongs to;
    ``counter_account`` is the balance-sheet accrual account it's periodized against. Which
    side is debited each period depends on ``account``'s class - see
    ``periodization.create_periodization_vouchers``.
    """

    description = forms.CharField(
        label="Beskrivning",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    amount = forms.DecimalField(
        label="Totalt belopp",
        min_value=Decimal("0.01"),
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
    )
    account = AccountChoiceField(
        label="Konto (kostnad/intäkt)",
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    counter_account = AccountChoiceField(
        label="Motkonto (17xx förutbetald kostnad / 29xx upplupen kostnad och förutbetald intäkt)",
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    months = forms.IntegerField(
        label="Antal månader",
        min_value=2,
        max_value=60,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "2", "max": "60"}),
    )
    start_date = forms.DateField(
        label="Startmånad",
        help_text="Valfritt datum i den första månaden - dagen används inte, hela månaden periodiseras.",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )

    #: Resultaträkning-klasser - de enda där "konto" (kostnaden/intäkten som periodiseras) hör hemma.
    RESULT_ACCOUNT_CLASSES = [
        AccountClass.REVENUE,
        AccountClass.COST_OF_GOODS,
        AccountClass.OTHER_EXTERNAL,
        AccountClass.OTHER_EXTERNAL_2,
        AccountClass.PERSONNEL,
        AccountClass.FINANCIAL,
    ]

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = Account.objects.filter(company=company, is_active=True).order_by("number")
        self.fields["account"].queryset = accounts.filter(account_class__in=self.RESULT_ACCOUNT_CLASSES)
        self.fields["counter_account"].queryset = accounts.filter(
            Q(number__startswith="17") | Q(number__startswith="29")
        )
        if not self.is_bound:
            self.fields["start_date"].initial = timezone.localdate().replace(day=1)

    def clean(self):
        cleaned_data = super().clean()
        account = cleaned_data.get("account")
        counter_account = cleaned_data.get("counter_account")
        if account is None or counter_account is None:
            return cleaned_data

        if account.account_class == AccountClass.REVENUE:
            if not counter_account.number.startswith("29"):
                self.add_error(
                    "counter_account",
                    "Ett intäktskonto periodiseras mot ett 29xx-konto (upplupen kostnad och förutbetald intäkt).",
                )
        elif not counter_account.number.startswith("17"):
            self.add_error(
                "counter_account",
                "Ett kostnadskonto periodiseras mot ett 17xx-konto (förutbetald kostnad).",
            )
        return cleaned_data


class BudgetForm(forms.Form):
    """Matrix-style form: one row per resultaträkning-account (klass 3-10), one column per
    calendar month, for a single accounting year.

    Not a formset - an account x month grid reads far better as a table than as a flat list
    of one-row-per-account-month forms, and the field set is entirely determined by the
    accounting year's company, so a plain Form with dynamically added fields is enough. Amounts
    use the same signed convention as the income statement (positive revenue, negative cost) -
    see BudgetLine's docstring.
    """

    def __init__(self, *args, company, accounting_year, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.accounting_year = accounting_year
        self.months = list(enumerate(MONTH_LABELS, start=1))
        # The BAS chart seeds ~780 klass 3-10 accounts per company; a field per account x month
        # would blow past DATA_UPLOAD_MAX_NUMBER_FIELDS on submit. Budgeting only makes sense for
        # accounts actually in use, so - like huvudboken (build_general_ledger_context) - restrict
        # to accounts with postings this year, plus any already budgeted.
        used_account_ids = set(
            JournalEntry.objects.filter(transaction__accounting_year=accounting_year).values_list(
                "account_id", flat=True
            )
        )
        budgeted_account_ids = set(
            BudgetLine.objects.filter(accounting_year=accounting_year).values_list("account_id", flat=True)
        )
        self.accounts = list(
            Account.objects.filter(
                company=company,
                is_active=True,
                account_class__in=BUDGET_ACCOUNT_CLASSES,
                pk__in=used_account_ids | budgeted_account_ids,
            ).order_by("number")
        )
        existing = {
            (line.account_id, line.month): line.amount
            for line in BudgetLine.objects.filter(accounting_year=accounting_year)
        }
        for account in self.accounts:
            for month, _label in self.months:
                initial = existing.get((account.pk, month))
                self.fields[budget_field_name(account.pk, month)] = forms.DecimalField(
                    label=f"{account.number} {MONTH_LABELS[month - 1]}",
                    required=False,
                    max_digits=15,
                    decimal_places=2,
                    initial=initial,
                    widget=forms.NumberInput(
                        attrs={"class": "form-control form-control-sm budget-cell", "step": "0.01"}
                    ),
                )

    @property
    def rows(self):
        """[(account, [boundfield, ...12 in month order]), ...] for template iteration."""
        return [
            (account, [self[budget_field_name(account.pk, month)] for month, _label in self.months])
            for account in self.accounts
        ]

    def save(self):
        with db_transaction.atomic():
            for account in self.accounts:
                for month, _label in self.months:
                    amount = self.cleaned_data.get(budget_field_name(account.pk, month))
                    if not amount:
                        BudgetLine.objects.filter(
                            accounting_year=self.accounting_year, account=account, month=month
                        ).delete()
                    else:
                        BudgetLine.objects.update_or_create(
                            company=self.company,
                            accounting_year=self.accounting_year,
                            account=account,
                            month=month,
                            defaults={"amount": amount},
                        )


class CompanyMemberForm(forms.Form):
    email = forms.EmailField(
        label="E-postadress",
        help_text="Personen måste först ha registrerat ett konto i SaldoVibe.",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    role = forms.ChoiceField(
        label="Roll",
        choices=CompanyMembership.Role.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        if not email:
            return cleaned
        user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            raise forms.ValidationError("Ingen användare med den e-postadressen. Be personen registrera sig först.")
        if self.company.users.filter(pk=user.pk).exists():
            raise forms.ValidationError("Användaren är redan kopplad till företaget.")
        cleaned["user"] = user
        return cleaned
