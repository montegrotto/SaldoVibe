import re

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from bookkeeping.form_utils import normalize_decimal_fields

from .models import Employee, EmployeeDefaultAdjustment, PayrollRun, SalaryAdjustment, SalaryRecord


class EmployeeForm(forms.ModelForm):
    # Deklareras explicit utan max_length så att bindestreck/mellanslag hinner
    # normaliseras bort i clean-steget innan modellens 12-teckensgräns valideras.
    personal_identity_number = forms.CharField(
        label="Personnummer",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ÅÅÅÅMMDDNNNN"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

    def clean_personal_identity_number(self):
        value = self.cleaned_data["personal_identity_number"].replace("-", "").replace(" ", "")
        if not (value.isdigit() and len(value) == 12):
            raise forms.ValidationError("Ange personnummer med 12 siffror (ÅÅÅÅMMDDNNNN).")
        return value

    class Meta:
        model = Employee
        fields = (
            "first_name",
            "last_name",
            "personal_identity_number",
            "address",
            "postal_code",
            "city",
            "monthly_salary",
            "employment_rate",
            "tax_table_number",
            "tax_table_column",
            "start_date",
            "is_active",
        )
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "personal_identity_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "ÅÅÅÅMMDDNNNN"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "monthly_salary": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "employment_rate": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}
            ),
            "tax_table_number": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "40"}),
            "tax_table_column": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

        help_texts = {
            "tax_table_number": "Ange anställds skattetabell enligt Skatteverket.",
            "tax_table_column": "Välj kolumn utifrån ersättningstyp och ålder. Se texten i listan för vad varje kolumn betyder.",
        }


class PayrollRunCreateForm(forms.ModelForm):
    period = forms.CharField(
        label="Period",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "type": "month",
                "min": "2000-01",
                "max": "2100-12",
                "placeholder": "ÅÅÅÅ-MM",
                "pattern": r"\d{4}-\d{2}",
            }
        ),
    )
    generate_salary_records = forms.BooleanField(
        label="Skapa löneposter för aktiva anställda",
        initial=True,
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = PayrollRun
        fields = ("period", "payment_date")
        widgets = {
            "payment_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["period"].initial = today.strftime("%Y-%m")
        self.fields["payment_date"].initial = today

    def clean_period(self):
        # Native type="month" ger "ÅÅÅÅ-MM"; Firefox desktop faller tillbaka till fritext.
        value = self.cleaned_data["period"].strip()
        match = re.fullmatch(r"(\d{4})-(\d{1,2})", value)
        if not match or not 2000 <= int(match[1]) <= 2100 or not 1 <= int(match[2]) <= 12:
            raise forms.ValidationError("Ange period som ÅÅÅÅ-MM.")
        self.instance.period_year = int(match[1])
        self.instance.period_month = int(match[2])
        return f"{self.instance.period_year}-{self.instance.period_month:02d}"


class SalaryRecordAdjustmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

    class Meta:
        model = SalaryRecord
        fields = (
            "gross_salary",
            "tax_table_number",
            "tax_table_column",
        )
        widgets = {
            "gross_salary": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "tax_table_number": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "40"}),
            "tax_table_column": forms.Select(attrs={"class": "form-select"}),
        }


class SalaryAdjustmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

    class Meta:
        model = SalaryAdjustment
        fields = ("category", "phase", "direction", "description", "amount")
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "phase": forms.Select(attrs={"class": "form-select"}),
            "direction": forms.Select(attrs={"class": "form-select"}),
            "description": forms.TextInput(attrs={"class": "form-control", "placeholder": "t.ex. OB-tillägg"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_taxable = (
            instance.phase == SalaryAdjustment.Phase.PRE_TAX
            and instance.direction == SalaryAdjustment.Direction.ADDITION
        )
        if commit:
            instance.save()
        return instance


class EmployeeDefaultAdjustmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

    class Meta:
        model = EmployeeDefaultAdjustment
        fields = ("category", "phase", "direction", "description", "amount")
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "phase": forms.Select(attrs={"class": "form-select"}),
            "direction": forms.Select(attrs={"class": "form-select"}),
            "description": forms.TextInput(attrs={"class": "form-control", "placeholder": "t.ex. OB-tillägg"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_taxable = (
            instance.phase == EmployeeDefaultAdjustment.Phase.PRE_TAX
            and instance.direction == EmployeeDefaultAdjustment.Direction.ADDITION
        )
        if commit:
            instance.save()
        return instance


SalaryAdjustmentFormSet = inlineformset_factory(
    SalaryRecord,
    SalaryAdjustment,
    form=SalaryAdjustmentForm,
    extra=0,
    can_delete=True,
)


EmployeeDefaultAdjustmentFormSet = inlineformset_factory(
    Employee,
    EmployeeDefaultAdjustment,
    form=EmployeeDefaultAdjustmentForm,
    extra=0,
    can_delete=True,
)
