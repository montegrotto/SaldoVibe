"""Posting logic for expense claims: registration into the ledger."""

from decimal import Decimal

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
        vat_amount=vat_amount,
        component_total=expense_amount,
        labels={
            "total_not_positive": "Totalbelopp måste vara större än 0 för att bokföra utlägget.",
            "component_sum_mismatch": "Belopp exkl. moms plus moms måste vara lika med totalbeloppet.",
            "period_locked": "Perioden för utläggsdatumet är låst. Bokför i öppen period eller lås upp perioden.",
        },
    )

    person = claim.person_display_name
    rows = [(claim.expense_account, expense_amount, Decimal("0.00"), f"Utlägg {person}")]
    if vat_amount > Decimal("0"):
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
