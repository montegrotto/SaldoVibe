"""Posting logic for fixed assets: depreciation, impairment, and corrections."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone

from bookkeeping.payables import add_journal_entry

from .models import (
    FixedAssetDepreciation,
    FixedAssetImpairment,
    FixedAssetType,
    _month_start,
    ensure_default_asset_types,
)


def resolve_accounting_year_for_period(asset, period):
    year = asset.company.accounting_years.filter(start_date__lte=period, end_date__gte=period).first()
    if year is None:
        raise ValidationError("Kan inte bokföra avskrivning. Inget räkenskapsår matchar avskrivningsperioden.")
    return year


def _resolve_depreciation_accounts(asset):
    ensure_default_asset_types(asset.company)
    asset_type = FixedAssetType.objects.filter(company=asset.company, key=asset.asset_type).first()
    if asset_type is None:
        raise ValidationError("Kan inte bokföra avskrivning. Tillgångstypen är inte konfigurerad.")

    if asset_type.depreciation_expense_account is None or asset_type.accumulated_depreciation_account is None:
        raise ValidationError(f"Kan inte bokföra avskrivning. Konton saknas för tillgångstypen '{asset_type.name}'.")

    return asset_type.depreciation_expense_account, asset_type.accumulated_depreciation_account


def _resolve_impairment_accounts(asset):
    ensure_default_asset_types(asset.company)
    asset_type = FixedAssetType.objects.filter(company=asset.company, key=asset.asset_type).first()
    if asset_type is None:
        raise ValidationError("Kan inte bokföra nedskrivning. Tillgångstypen är inte konfigurerad.")
    if asset_type.impairment_expense_account is None or asset_type.accumulated_impairment_account is None:
        raise ValidationError(
            f"Kan inte bokföra nedskrivning. Konton för nedskrivning saknas för tillgångstypen '{asset_type.name}'."
        )
    return asset_type.impairment_expense_account, asset_type.accumulated_impairment_account


def create_swing_transaction(asset, period, amount, user, description, expense_account, accumulated_account):
    from bookkeeping.models import Transaction, TransactionSource
    from bookkeeping.period_locking import is_date_locked

    if is_date_locked(asset.company, period):
        raise ValidationError("Perioden för bokföringsdatumet är låst. Bokför i öppen period eller lås upp perioden.")
    accounting_year = resolve_accounting_year_for_period(asset, period)
    txn = Transaction.objects.create(
        accounting_year=accounting_year,
        date=period,
        description=description,
        reference=f"FA-{asset.pk}-{period.strftime('%Y%m')}-{timezone.now().strftime('%H%M%S%f')}",
        created_by=user,
        source=TransactionSource.FIXED_ASSET,
    )
    debit_account, credit_account = (
        (expense_account, accumulated_account)
        if amount >= 0
        else (
            accumulated_account,
            expense_account,
        )
    )
    posted_amount = abs(amount)
    add_journal_entry(
        transaction=txn,
        account=debit_account,
        debit=posted_amount,
        credit=Decimal("0.00"),
        description=description,
    )
    add_journal_entry(
        transaction=txn,
        account=credit_account,
        debit=Decimal("0.00"),
        credit=posted_amount,
        description=description,
    )
    txn.validate_balanced()
    return txn


def _create_depreciation_transaction(asset, period, amount, user, description=None):
    expense_account, accumulated_account = _resolve_depreciation_accounts(asset)
    return create_swing_transaction(
        asset,
        period,
        amount,
        user,
        description or f"Månadsavskrivning {asset.name}",
        expense_account,
        accumulated_account,
    )


def register_monthly_depreciation(asset, period=None, user=None):
    if asset.disposed_at:
        raise ValidationError("Tillgången är avyttrad/utrangerad och kan inte skrivas av vidare.")
    if asset.is_fully_depreciated:
        raise ValidationError("Tillgången är redan fullt avskriven.")

    expected_period = asset.next_depreciation_date
    if period is None:
        period = expected_period
    period = _month_start(period)
    if period != expected_period:
        raise ValidationError("Du kan bara registrera nästa planerade avskrivningsmånad.")

    amount = asset.next_depreciation_amount()
    if amount <= Decimal("0.00"):
        raise ValidationError("Det finns inget återstående belopp att skriva av.")

    with db_transaction.atomic():
        txn = _create_depreciation_transaction(asset, period, amount, user)
        return FixedAssetDepreciation.objects.create(
            fixed_asset=asset,
            period=period,
            amount=amount,
            transaction=txn,
        )


def register_depreciation_correction(asset, original_entry, amount, reason, user=None):
    if original_entry.fixed_asset_id != asset.pk:
        raise ValidationError("Avskrivningsposten tillhör inte denna tillgång.")
    if original_entry.is_correction:
        raise ValidationError("Det går inte att korrigera en korrigering. Korrigera ursprungsposten.")
    if amount == Decimal("0.00"):
        raise ValidationError({"amount": "Korrigeringsbeloppet kan inte vara noll."})
    if not reason:
        raise ValidationError({"reason": "Ange en anledning till korrigeringen."})

    with db_transaction.atomic():
        txn = _create_depreciation_transaction(
            asset,
            original_entry.period,
            amount,
            user,
            description=f"Korrigering av avskrivning {asset.name} ({original_entry.period:%Y-%m})",
        )
        return FixedAssetDepreciation.objects.create(
            fixed_asset=asset,
            period=original_entry.period,
            amount=amount,
            transaction=txn,
            is_correction=True,
            correction_of=original_entry,
            reason=reason,
        )


def register_impairment(asset, period, amount, reason, user=None):
    if asset.disposed_at:
        raise ValidationError("Tillgången är avyttrad/utrangerad och kan inte skrivas ned.")
    if amount <= Decimal("0.00"):
        raise ValidationError({"amount": "Nedskrivningsbelopp måste vara större än 0."})
    if not reason:
        raise ValidationError({"reason": "Ange en anledning till nedskrivningen."})
    if amount > asset.current_book_value:
        raise ValidationError({"amount": "Nedskrivningsbeloppet kan inte vara högre än det bokförda värdet."})

    expense_account, accumulated_account = _resolve_impairment_accounts(asset)
    period = _month_start(period)
    with db_transaction.atomic():
        txn = create_swing_transaction(
            asset,
            period,
            amount,
            user,
            f"Nedskrivning {asset.name}",
            expense_account,
            accumulated_account,
        )
        return FixedAssetImpairment.objects.create(
            fixed_asset=asset,
            period=period,
            amount=amount,
            transaction=txn,
            reason=reason,
        )


def register_impairment_correction(asset, original_entry, amount, reason, user=None):
    if original_entry.fixed_asset_id != asset.pk:
        raise ValidationError("Nedskrivningsposten tillhör inte denna tillgång.")
    if original_entry.is_correction:
        raise ValidationError("Det går inte att korrigera en korrigering. Korrigera ursprungsposten.")
    if amount == Decimal("0.00"):
        raise ValidationError({"amount": "Korrigeringsbeloppet kan inte vara noll."})
    if not reason:
        raise ValidationError({"reason": "Ange en anledning till korrigeringen."})

    expense_account, accumulated_account = _resolve_impairment_accounts(asset)
    with db_transaction.atomic():
        txn = create_swing_transaction(
            asset,
            original_entry.period,
            amount,
            user,
            f"Korrigering av nedskrivning {asset.name} ({original_entry.period:%Y-%m})",
            expense_account,
            accumulated_account,
        )
        return FixedAssetImpairment.objects.create(
            fixed_asset=asset,
            period=original_entry.period,
            amount=amount,
            transaction=txn,
            is_correction=True,
            correction_of=original_entry,
            reason=reason,
        )


def create_disposal_transaction(asset, disposal_date, *, sale_price, proceeds_account, user):
    """Bokar bort tillgången: kredit tillgångskonto, debet ack. avskrivning/nedskrivning,
    debet försäljningspris, och skillnaden mot bokfört värde på vinst-/förlustkontot."""
    from bookkeeping.models import Transaction, TransactionSource
    from bookkeeping.period_locking import is_date_locked

    if is_date_locked(asset.company, disposal_date):
        raise ValidationError("Perioden för avgångsdatumet är låst. Bokför i öppen period eller lås upp perioden.")
    ensure_default_asset_types(asset.company)
    asset_type = FixedAssetType.objects.filter(company=asset.company, key=asset.asset_type).first()
    if asset_type is None:
        raise ValidationError("Kan inte bokföra avgången. Tillgångstypen är inte konfigurerad.")

    def required(field_name):
        account = getattr(asset_type, field_name)
        if account is None:
            label = FixedAssetType._meta.get_field(field_name).verbose_name
            raise ValidationError(
                f"Kan inte bokföra avgången. Tillgångstypen '{asset_type.name}' saknar {label.lower()} "
                "(Anläggningstillgångar → Typer)."
            )
        return account

    depreciated = asset.total_depreciated
    impaired = asset.total_impaired
    result = sale_price - (asset.acquisition_value - depreciated - impaired)

    # (konto, debet, kredit) – nollrader hoppas över.
    rows = [(required("asset_account"), Decimal("0.00"), asset.acquisition_value)]
    if depreciated:
        rows.append((required("accumulated_depreciation_account"), depreciated, Decimal("0.00")))
    if impaired:
        rows.append((required("accumulated_impairment_account"), impaired, Decimal("0.00")))
    if sale_price:
        rows.append((proceeds_account, sale_price, Decimal("0.00")))
    if result > 0:
        rows.append((required("disposal_gain_account"), Decimal("0.00"), result))
    elif result < 0:
        rows.append((required("disposal_loss_account"), -result, Decimal("0.00")))

    description = f"Avgång {asset.name}"
    with db_transaction.atomic():
        txn = Transaction.objects.create(
            accounting_year=resolve_accounting_year_for_period(asset, disposal_date),
            date=disposal_date,
            description=description,
            reference=f"FA-{asset.pk}-avgang",
            created_by=user,
            source=TransactionSource.FIXED_ASSET,
        )
        for account, debit, credit in rows:
            add_journal_entry(transaction=txn, account=account, debit=debit, credit=credit, description=description)
        txn.validate_balanced()
    return txn
