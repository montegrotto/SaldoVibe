from decimal import Decimal

from django import forms

from bookkeeping.form_utils import normalize_decimal_fields
from bookkeeping.models import AccountClass

from .models import ExpenseClaim


class ExpenseClaimForm(forms.ModelForm):
    class Meta:
        model = ExpenseClaim
        fields = (
            "employee",
            "person_name",
            "description",
            "expense_date",
            "expense_account",
            "total_amount",
            "vat_amount",
        )
        widgets = {
            "employee": forms.Select(attrs={"class": "form-select"}),
            "person_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Om utlägget gjorts av någon annan än en anställd"}
            ),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "expense_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "expense_account": forms.Select(attrs={"class": "form-select"}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
            "vat_amount": forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        self.company = company
        super().__init__(*args, **kwargs)
        normalize_decimal_fields(self)

        self.fields["employee"].required = False
        self.fields["person_name"].required = False

        if company is not None:
            self.fields["employee"].queryset = company.employees.filter(is_active=True).order_by(
                "first_name", "last_name"
            )
            self.fields["expense_account"].queryset = (
                company.accounts.filter(is_active=True)
                .exclude(
                    account_class__in=(
                        AccountClass.ASSET,
                        AccountClass.EQUITY_LIABILITY,
                        AccountClass.REVENUE,
                    )
                )
                .order_by("number")
            )

        self.fields["employee"].empty_label = "Välj anställd…"

    def _get_default_account(self, *numbers):
        if self.company is None:
            return None
        for number in numbers:
            account = self.company.accounts.filter(is_active=True, number=number).first()
            if account is not None:
                return account
        return None

    def clean(self):
        cleaned_data = super().clean()

        total_amount = cleaned_data.get("total_amount") or Decimal("0")
        vat_amount = cleaned_data.get("vat_amount") or Decimal("0")
        employee = cleaned_data.get("employee")
        person_name = (cleaned_data.get("person_name") or "").strip()
        expense_date = cleaned_data.get("expense_date")

        if not employee and not person_name:
            self.add_error("employee", "Ange anställd eller namn på den som gjort utlägget.")

        if total_amount <= Decimal("0"):
            self.add_error("total_amount", "Totalbelopp måste vara större än 0.")

        if vat_amount < Decimal("0"):
            self.add_error("vat_amount", "Moms måste vara 0 eller större.")

        if vat_amount > total_amount:
            self.add_error("vat_amount", "Moms kan inte vara större än totalbeloppet.")

        liability_account = self._get_default_account(*ExpenseClaim.DEFAULT_LIABILITY_ACCOUNT_NUMBERS)
        if liability_account is None:
            self.add_error(None, "Standardkonto 2820 (eller 2890) saknas i kontoplanen.")
        else:
            self.instance.liability_account = liability_account

        if vat_amount > Decimal("0"):
            vat_account = self._get_default_account(ExpenseClaim.DEFAULT_VAT_ACCOUNT_NUMBER)
            if vat_account is None:
                self.add_error(None, "Standardkonto 2640 saknas i kontoplanen.")
            else:
                self.instance.vat_account = vat_account
        else:
            self.instance.vat_account = None

        if expense_date:
            accounting_year = None
            if self.company is not None:
                accounting_year = self.company.accounting_years.filter(
                    start_date__lte=expense_date,
                    end_date__gte=expense_date,
                ).first()
            if accounting_year is None:
                self.add_error("expense_date", "Inget räkenskapsår matchar utläggsdatumet.")
            else:
                self.instance.accounting_year = accounting_year

        self.instance.amount_ex_vat = total_amount - vat_amount

        return cleaned_data
