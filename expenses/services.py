"""Posting logic for expense claims: registration into the ledger."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from bookkeeping.payables import post_payable_registration, validate_payable_registration
from bookkeeping.payables import quantize_amount as _amount


def register_and_bookkeep_expense_claim(claim, user):
    if claim.is_registered:
        return claim.registered_transaction

    from bookkeeping.models import TransactionSource

    vat_amount = _amount(claim.vat_amount)
    total_amount = _amount(claim.total_amount)
    expense_amount = _amount(claim.amount_ex_vat)

    validate_payable_registration(
        claim,
        date=claim.expense_date,
        total_amount=total_amount,
        # Vid omvänd skattskyldighet ingår momsen inte i det betalda beloppet: kostnaden är hela totalen.
        vat_amount=Decimal("0.00") if claim.is_reverse_charge else vat_amount,
        component_total=expense_amount,
        labels={
            "total_not_positive": "Totalbelopp måste vara större än 0 för att bokföra utlägget.",
            "component_sum_mismatch": "Belopp exkl. moms plus moms måste vara lika med totalbeloppet.",
            "period_locked": "Perioden för utläggsdatumet är låst. Bokför i öppen period eller lås upp perioden.",
        },
    )

    person = claim.person_display_name
    rows = [(claim.expense_account, expense_amount, Decimal("0.00"), f"Utlägg {person}")]
    if vat_amount > Decimal("0") and claim.is_reverse_charge:
        rows += _reverse_charge_vat_rows(claim, vat_amount, person)
    elif vat_amount > Decimal("0"):
        rows.append((claim.vat_account, vat_amount, Decimal("0.00"), f"Ingående moms utlägg {person}"))
    rows.append((claim.liability_account, Decimal("0.00"), total_amount, f"Skuld utlägg {person}"))

    return post_payable_registration(
        claim,
        user,
        date=claim.expense_date,
        description=f"Utlägg {person}: {claim.description}",
        source=TransactionSource.EXPENSE,
        rows=rows,
    )


def _reverse_charge_vat_rows(claim, vat_amount, person):
    """Beräknad moms på inköp från utlandet: utgående 2614 (ruta 30) mot beräknad ingående 2645
    (ruta 48). Nettot är noll, men båda rutorna ska redovisas."""
    output_vat_account = claim.company.accounts.filter(
        is_active=True, number=claim.REVERSE_CHARGE_OUTPUT_VAT_ACCOUNT_NUMBER
    ).first()
    if claim.vat_account is None or output_vat_account is None:
        raise ValidationError("Standardkonto 2614 och 2645 krävs i kontoplanen för omvänd skattskyldighet.")
    return [
        (claim.vat_account, vat_amount, Decimal("0.00"), f"Beräknad ingående moms utlägg {person}"),
        (output_vat_account, Decimal("0.00"), vat_amount, f"Utgående moms omvänd skattskyldighet utlägg {person}"),
    ]
