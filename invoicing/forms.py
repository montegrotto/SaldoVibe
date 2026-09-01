from decimal import Decimal

from django import forms
from django.forms import modelformset_factory

from bookkeeping.form_utils import normalize_decimal_fields
from bookkeeping.models import AccountClass

from .models import (
    Article,
    Customer,
    Invoice,
    InvoiceLine,
    RecurringInvoice,
    RecurringInvoiceDueDateMode,
    RecurringInvoiceInterval,
    RecurringInvoiceLine,
)


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name",
            "org_number",
            "vat_number",
            "email",
            "phone",
            "address",
            "postal_code",
            "city",
            "country_code",
            "default_payment_terms_days",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "org_number": forms.TextInput(attrs={"class": "form-control"}),
            "vat_number": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "country_code": forms.TextInput(
                attrs={"class": "form-control text-uppercase", "maxlength": "2", "placeholder": "SE"}
            ),
            "default_payment_terms_days": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ArticleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        if company is not None:
            self.fields["income_account"].queryset = company.accounts.filter(
                is_active=True,
                account_class=AccountClass.REVENUE,
            ).order_by("number")

    def clean_income_account(self):
        income_account = self.cleaned_data.get("income_account")
        if income_account is None:
            raise forms.ValidationError("Intäktskonto måste anges för artikeln.")
        return income_account

    class Meta:
        model = Article
        fields = [
            "article_number",
            "name",
            "description",
            "unit",
            "unit_price",
            "vat_rate",
            "income_account",
            "is_active",
        ]
        widgets = {
            "article_number": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01"}),
            "vat_rate": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01"}),
            "income_account": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class ReminderPrintForm(forms.Form):
    """GET-parametrarna för påminnelseutskriften. Båda är valfria — vyn faller
    tillbaka på 0 kr respektive tio dagar framåt."""

    avgift = forms.DecimalField(min_value=Decimal("0.00"), max_digits=10, decimal_places=2, required=False)
    betala_senast = forms.DateField(required=False)


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            "customer",
            "invoice_date",
            "due_date",
            "delivery_date",
            "reference",
            "reverse_charge",
            "notes",
        ]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-select"}),
            "invoice_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "delivery_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "reverse_charge": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        if company is not None:
            self.fields["customer"].queryset = company.customers.filter(is_active=True).order_by("name")


class LineTypeCleanMixin:
    """Shared clean() for line forms that support both billable rows and free-text rows."""

    def clean(self):
        cleaned_data = super().clean()
        line_type = cleaned_data.get("line_type") or InvoiceLine.LINE_TYPE_ITEM
        description = (cleaned_data.get("description") or "").strip()
        if line_type == InvoiceLine.LINE_TYPE_TEXT:
            if not description:
                self.add_error("description", "Textraden måste ha ett innehåll.")
            cleaned_data["article"] = None
            cleaned_data["quantity"] = Decimal("0.00")
            cleaned_data["unit"] = ""
            cleaned_data["unit_price"] = Decimal("0.00")
            cleaned_data["vat_rate"] = Decimal("0.00")
        else:
            if cleaned_data.get("quantity") is None:
                self.add_error("quantity", "Antal måste anges.")
            if cleaned_data.get("unit_price") is None:
                self.add_error("unit_price", "Á-pris måste anges.")
        return cleaned_data


class InvoiceLineForm(LineTypeCleanMixin, forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = ["article", "description", "quantity", "unit", "unit_price", "vat_rate", "sort_order", "line_type"]
        widgets = {
            "article": forms.Select(attrs={"class": "form-select form-select-sm invoice-article-select"}),
            "description": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01"}),
            "unit": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01"}),
            "vat_rate": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm text-end",
                    "step": "0.01",
                    "readonly": "readonly",
                    "tabindex": "-1",
                }
            ),
            "sort_order": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "0"}),
            "line_type": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        self.fields["quantity"].required = False
        self.fields["unit"].required = False
        self.fields["unit_price"].required = False
        self.fields["vat_rate"].required = False
        if company is not None:
            self.fields["article"].queryset = company.articles.filter(is_active=True).order_by("article_number", "name")


InvoiceLineFormSet = modelformset_factory(
    InvoiceLine,
    form=InvoiceLineForm,
    extra=1,
    can_delete=True,
)


def build_invoice_line_formset(*, company, data=None, queryset=None, prefix="lines"):
    if queryset is None:
        queryset = InvoiceLine.objects.none()
    return InvoiceLineFormSet(
        data=data,
        queryset=queryset,
        prefix=prefix,
        form_kwargs={"company": company},
    )


DUE_DATE_DAY_CHOICES = (
    [("", "Välj dag")] + [(str(day), str(day)) for day in range(1, 32)] + [("last", "Sista dagen i månaden")]
)


class RecurringInvoiceForm(forms.ModelForm):
    due_date_day_choice = forms.ChoiceField(
        choices=DUE_DATE_DAY_CHOICES,
        required=False,
        label="Förfaller dag",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = RecurringInvoice
        fields = [
            "name",
            "customer",
            "interval",
            "custom_interval_months",
            "start_date",
            "anchor_to_month_end",
            "end_date",
            "due_date_mode",
            "payment_terms_days",
            "period_reference",
            "reference",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "customer": forms.Select(attrs={"class": "form-select"}),
            "interval": forms.Select(attrs={"class": "form-select"}),
            "custom_interval_months": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "anchor_to_month_end": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date_mode": forms.Select(attrs={"class": "form-select"}),
            "period_reference": forms.Select(attrs={"class": "form-select"}),
            "payment_terms_days": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        if company is not None:
            self.fields["customer"].queryset = company.customers.filter(is_active=True).order_by("name")
        if self.instance and self.instance.pk:
            if self.instance.due_date_last_day_of_month:
                self.fields["due_date_day_choice"].initial = "last"
            elif self.instance.due_date_day_of_month:
                self.fields["due_date_day_choice"].initial = str(self.instance.due_date_day_of_month)

    def clean(self):
        cleaned_data = super().clean()
        interval = cleaned_data.get("interval")
        custom_interval_months = cleaned_data.get("custom_interval_months")
        if interval == RecurringInvoiceInterval.CUSTOM_MONTHS and not custom_interval_months:
            self.add_error("custom_interval_months", "Ange eget antal månader för det valda intervallet.")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Slutdatum kan inte vara före startdatum.")
        # The instance is mutated here (rather than in save()) because ModelForm's
        # _post_clean() runs Model.clean() right after this method returns, using
        # whatever is already set on self.instance at that point.
        due_date_mode = cleaned_data.get("due_date_mode")
        if due_date_mode == RecurringInvoiceDueDateMode.DAY_OF_MONTH:
            day_choice = cleaned_data.get("due_date_day_choice")
            if not day_choice:
                self.add_error("due_date_day_choice", "Ange vilken dag i månaden fakturan ska förfalla.")
            elif day_choice == "last":
                self.instance.due_date_last_day_of_month = True
                self.instance.due_date_day_of_month = None
            else:
                self.instance.due_date_last_day_of_month = False
                self.instance.due_date_day_of_month = int(day_choice)
        else:
            self.instance.due_date_last_day_of_month = False
            self.instance.due_date_day_of_month = None
        return cleaned_data


class RecurringInvoiceLineForm(LineTypeCleanMixin, forms.ModelForm):
    class Meta:
        model = RecurringInvoiceLine
        fields = ["article", "description", "quantity", "unit", "unit_price", "vat_rate", "sort_order", "line_type"]
        widgets = {
            "article": forms.Select(attrs={"class": "form-select form-select-sm invoice-article-select"}),
            "description": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01"}),
            "unit": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control form-control-sm text-end", "step": "0.01"}),
            "vat_rate": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm text-end",
                    "step": "0.01",
                    "readonly": "readonly",
                    "tabindex": "-1",
                }
            ),
            "sort_order": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "0"}),
            "line_type": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        self.fields["quantity"].required = False
        self.fields["unit"].required = False
        self.fields["unit_price"].required = False
        self.fields["vat_rate"].required = False
        if company is not None:
            self.fields["article"].queryset = company.articles.filter(is_active=True).order_by("article_number", "name")


RecurringInvoiceLineFormSet = modelformset_factory(
    RecurringInvoiceLine,
    form=RecurringInvoiceLineForm,
    extra=1,
    can_delete=True,
)


def build_recurring_invoice_line_formset(*, company, data=None, queryset=None, prefix="lines"):
    if queryset is None:
        queryset = RecurringInvoiceLine.objects.none()
    return RecurringInvoiceLineFormSet(
        data=data,
        queryset=queryset,
        prefix=prefix,
        form_kwargs={"company": company},
    )
