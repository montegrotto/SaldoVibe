from decimal import Decimal

from django import forms
from django.forms import formset_factory

from bookkeeping.form_utils import normalize_decimal_fields

from .models import Supplier, SupplierInvoice


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        single_clean = super().clean
        return [single_clean(file_obj, initial) for file_obj in files]


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ("name", "org_number", "email", "phone", "bankgiro", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "org_number": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "bankgiro": forms.TextInput(attrs={"class": "form-control", "placeholder": "t.ex. 123-4567"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class SupplierInvoiceForm(forms.ModelForm):
    class Meta:
        model = SupplierInvoice
        fields = (
            "supplier",
            "invoice_number",
            "ocr_code",
            "invoice_date",
            "due_date",
            "total_amount",
            "vat_amount",
        )
        widgets = {
            "supplier": forms.Select(attrs={"class": "form-select"}),
            "invoice_number": forms.TextInput(attrs={"class": "form-control"}),
            "ocr_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "Valfri OCR-kod"}),
            "invoice_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
            "vat_amount": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        self.company = company
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

        self.fields["invoice_number"].required = False
        self.fields["ocr_code"].required = False

        if company is not None:
            suppliers = company.suppliers.filter(is_active=True).order_by("name")
            self.fields["supplier"].queryset = suppliers

        self.fields["supplier"].empty_label = "Välj leverantör…"

    def _get_accounting_year_for_invoice_date(self, invoice_date):
        if self.company is None or invoice_date is None:
            return None

        return self.company.accounting_years.filter(
            start_date__lte=invoice_date,
            end_date__gte=invoice_date,
        ).first()

    def _get_default_account(self, number):
        if self.company is None:
            return None
        return self.company.accounts.filter(is_active=True, number=number).first()

    def clean(self):
        cleaned_data = super().clean()

        total_amount = cleaned_data.get("total_amount") or Decimal("0")
        vat_amount = cleaned_data.get("vat_amount") or Decimal("0")
        supplier = cleaned_data.get("supplier")
        invoice_date = cleaned_data.get("invoice_date")

        payable_account = self._get_default_account("2440")
        vat_account = self._get_default_account("2640")

        if payable_account is None:
            self.add_error(None, "Standardkonto 2440 saknas i kontoplanen.")
        else:
            self.instance.payable_account = payable_account

        if total_amount <= Decimal("0"):
            self.add_error("total_amount", "Totalbelopp måste vara större än 0.")

        if vat_amount < Decimal("0"):
            self.add_error("vat_amount", "Moms måste vara 0 eller större.")

        if vat_amount > total_amount:
            self.add_error("vat_amount", "Moms kan inte vara större än totalbeloppet.")

        if vat_amount > Decimal("0"):
            if vat_account is None:
                self.add_error(None, "Standardkonto 2640 saknas i kontoplanen.")
            else:
                self.instance.vat_account = vat_account
        else:
            self.instance.vat_account = None

        if not supplier:
            self.add_error("supplier", "Leverantör måste anges.")

        if invoice_date:
            accounting_year = self._get_accounting_year_for_invoice_date(invoice_date)
            if accounting_year is None:
                self.add_error("invoice_date", "Inget räkenskapsår matchar fakturadatumet.")
            else:
                self.instance.accounting_year = accounting_year

        return cleaned_data


class SupplierInvoiceAttachmentUploadForm(forms.Form):
    attachment_files = MultipleFileField(
        label="Bilagor",
        required=False,
        widget=MultipleFileInput(attrs={"class": "form-control", "accept": ".pdf,.png,.jpg,.jpeg"}),
    )

    def clean_attachment_files(self):
        files = self.cleaned_data.get("attachment_files") or []
        allowed_types = {"application/pdf", "image/png", "image/jpeg"}
        for file_obj in files:
            content_type = getattr(file_obj, "content_type", None)
            if content_type and content_type not in allowed_types:
                raise forms.ValidationError("Endast PDF, PNG eller JPEG är tillåtet.")
        return files


class SupplierInvoiceCostLineForm(forms.Form):
    expense_account = forms.ModelChoiceField(
        queryset=None,
        label="Kostnadskonto",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    debit = forms.DecimalField(
        label="Debet",
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=15,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
    )
    credit = forms.DecimalField(
        label="Kredit",
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=15,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
    )

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)
        if company is not None:
            self.fields["expense_account"].queryset = company.accounts.filter(
                is_active=True,
            ).order_by("number")

    def clean(self):
        cleaned_data = super().clean()
        debit = cleaned_data.get("debit") or Decimal("0")
        credit = cleaned_data.get("credit") or Decimal("0")
        expense_account = cleaned_data.get("expense_account")

        if debit > 0 and credit > 0:
            raise forms.ValidationError("En kostnadsrad kan inte ha belopp i både debet och kredit.")

        if expense_account and debit == 0 and credit == 0:
            raise forms.ValidationError("Ange ett belopp i antingen debet eller kredit.")

        return cleaned_data


SupplierInvoiceCostLineFormSet = formset_factory(
    SupplierInvoiceCostLineForm,
    extra=1,
    can_delete=True,
)


def build_cost_line_formset(*, company, data=None, prefix="cost_lines"):
    return SupplierInvoiceCostLineFormSet(
        data=data,
        prefix=prefix,
        form_kwargs={"company": company},
    )
