from decimal import Decimal

from django import forms

from bookkeeping.models import Account

from .models import NON_DEPRECIABLE_ASSET_TYPE_KEYS, FixedAsset, FixedAssetType, ensure_default_asset_types


class SwedishDecimalField(forms.DecimalField):
    """Accept both Swedish comma decimals and dot decimals."""

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(" ", "").replace(",", ".")
        return super().to_python(value)


class FixedAssetForm(forms.ModelForm):
    useful_life_years = forms.IntegerField(
        label="Nyttjandeperiod (år)",
        min_value=0,
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": "0",
                "placeholder": "År",
                "title": "År",
                "aria-label": "År",
            }
        ),
    )
    useful_life_extra_months = forms.IntegerField(
        label="Nyttjandeperiod (mån)",
        min_value=0,
        max_value=11,
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": "0",
                "max": "11",
                "placeholder": "Mån",
                "title": "Månader",
                "aria-label": "Månader",
            }
        ),
    )
    asset_type = forms.ChoiceField(
        label="Typ",
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    acquisition_value = SwedishDecimalField(
        min_value=0.01,
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0.01"}),
    )
    salvage_value = SwedishDecimalField(
        min_value=0,
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0"}),
    )
    reclassification_reason = forms.CharField(
        label="Anledning till omklassificering",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    class Meta:
        model = FixedAsset
        fields = (
            "name",
            "asset_type",
            "acquisition_date",
            "depreciation_start_date",
            "acquisition_value",
            "salvage_value",
            "useful_life_months",
            "is_active",
            "notes",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "acquisition_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "depreciation_start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "useful_life_months": forms.HiddenInput(),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        self.non_depreciable_keys = sorted(NON_DEPRECIABLE_ASSET_TYPE_KEYS)

        self.fields["depreciation_start_date"].required = False
        self.fields["salvage_value"].required = False
        self.fields["useful_life_months"].required = False
        self.fields["useful_life_years"].required = False
        self.fields["useful_life_extra_months"].required = False
        self.fields["asset_type"].widget.attrs["data-non-depreciable-keys"] = ",".join(self.non_depreciable_keys)

        total_months = None
        if getattr(self.instance, "pk", None):
            total_months = self.instance.useful_life_months
        elif self.initial.get("useful_life_months"):
            total_months = self.initial.get("useful_life_months")

        if total_months:
            self.fields["useful_life_years"].initial = int(total_months) // 12
            self.fields["useful_life_extra_months"].initial = int(total_months) % 12

        if self.company is not None:
            ensure_default_asset_types(self.company)
            type_qs = self.company.fixed_asset_types.order_by("sort_order", "name")
            self.fields["asset_type"].choices = [
                ("", "Välj tillgångstyp"),
                *[(type_obj.key, type_obj.name) for type_obj in type_qs if type_obj.is_active],
            ]

            current_value = getattr(self.instance, "asset_type", "")
            if current_value and current_value not in dict(self.fields["asset_type"].choices):
                existing = self.company.fixed_asset_types.filter(key=current_value).first()
                label = existing.name if existing else current_value
                self.fields["asset_type"].choices = [
                    ("", "Välj tillgångstyp"),
                    (current_value, label),
                    *[choice for choice in self.fields["asset_type"].choices if choice[0] != ""],
                ]

        if not self.fields["asset_type"].choices:
            self.fields["asset_type"].choices = [("", "Skapa först en tillgångstyp")]

    def clean_asset_type(self):
        value = self.cleaned_data["asset_type"]
        if self.company is None:
            return value

        if not self.company.fixed_asset_types.filter(key=value).exists():
            raise forms.ValidationError("Vald tillgångstyp finns inte för aktivt företag.")
        return value

    def clean(self):
        cleaned_data = super().clean()

        asset_type = cleaned_data.get("asset_type")
        acquisition_value = cleaned_data.get("acquisition_value")
        acquisition_date = cleaned_data.get("acquisition_date")

        if asset_type in NON_DEPRECIABLE_ASSET_TYPE_KEYS:
            if acquisition_value is not None:
                cleaned_data["salvage_value"] = acquisition_value
            if acquisition_date is not None:
                cleaned_data["depreciation_start_date"] = acquisition_date
            cleaned_data["useful_life_months"] = 1
            return cleaned_data

        years = cleaned_data.get("useful_life_years") or 0
        extra_months = cleaned_data.get("useful_life_extra_months") or 0
        total_months = years * 12 + extra_months

        legacy_total_months = cleaned_data.get("useful_life_months") or 0
        if total_months <= 0 and legacy_total_months > 0:
            total_months = legacy_total_months

        cleaned_data["useful_life_months"] = total_months

        if cleaned_data.get("salvage_value") is None:
            cleaned_data["salvage_value"] = Decimal("0.00")

        if cleaned_data.get("depreciation_start_date") is None:
            self.add_error("depreciation_start_date", "Ange start avskrivning.")
        if total_months <= 0:
            self.add_error("useful_life_years", "Ange nyttjandeperiod i år och/eller månader.")

        return cleaned_data


class FixedAssetTypeForm(forms.ModelForm):
    class Meta:
        model = FixedAssetType
        fields = (
            "name",
            "key",
            "depreciation_expense_account",
            "accumulated_depreciation_account",
            "impairment_expense_account",
            "accumulated_impairment_account",
            "asset_account",
            "disposal_gain_account",
            "disposal_loss_account",
            "sort_order",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "key": forms.TextInput(attrs={"class": "form-control", "placeholder": "t.ex. equipment"}),
            "depreciation_expense_account": forms.Select(attrs={"class": "form-select"}),
            "accumulated_depreciation_account": forms.Select(attrs={"class": "form-select"}),
            "impairment_expense_account": forms.Select(attrs={"class": "form-select"}),
            "accumulated_impairment_account": forms.Select(attrs={"class": "form-select"}),
            "asset_account": forms.Select(attrs={"class": "form-select"}),
            "disposal_gain_account": forms.Select(attrs={"class": "form-select"}),
            "disposal_loss_account": forms.Select(attrs={"class": "form-select"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if self.company is not None:
            self.instance.company = self.company
            account_qs = self.company.accounts.filter(is_active=True).order_by("number")
            for field_name in FixedAssetType.ACCOUNT_FIELDS:
                self.fields[field_name].queryset = account_qs


class FixedAssetImpairmentForm(forms.Form):
    period = forms.DateField(
        label="Period",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    amount = SwedishDecimalField(
        label="Nedskrivningsbelopp",
        min_value=0.01,
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01", "min": "0.01"}),
    )
    reason = forms.CharField(
        label="Anledning",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )


class FixedAssetDepreciationCorrectionForm(forms.Form):
    amount = SwedishDecimalField(
        label="Korrigeringsbelopp",
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01"}),
    )
    reason = forms.CharField(
        label="Anledning",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount == 0:
            raise forms.ValidationError("Korrigeringsbeloppet kan inte vara noll.")
        return amount


class FixedAssetImpairmentCorrectionForm(forms.Form):
    amount = SwedishDecimalField(
        label="Korrigeringsbelopp",
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control text-end", "step": "0.01"}),
    )
    reason = forms.CharField(
        label="Anledning",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount == 0:
            raise forms.ValidationError("Korrigeringsbeloppet kan inte vara noll.")
        return amount


class FixedAssetDisposalForm(forms.Form):
    disposal_date = forms.DateField(
        label="Datum",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    disposal_type = forms.ChoiceField(
        label="Typ av avgång",
        choices=FixedAsset.DisposalType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    disposal_reason = forms.CharField(
        label="Anledning",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    sale_price = forms.DecimalField(
        label="Försäljningspris (exkl. moms)",
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    proceeds_account = forms.ModelChoiceField(
        label="Konto för försäljningspriset",
        required=False,
        queryset=Account.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company is not None:
            account_qs = company.accounts.filter(is_active=True).order_by("number")
            self.fields["proceeds_account"].queryset = account_qs
            self.fields["proceeds_account"].initial = account_qs.filter(number="1930").first()
