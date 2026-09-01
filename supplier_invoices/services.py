"""Posting logic for supplier invoices: registration into the ledger."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from bookkeeping.payables import post_payable_registration, validate_payable_registration


def register_and_bookkeep_supplier_invoice(invoice, user):
    if invoice.is_registered:
        return invoice.registered_transaction

    from bookkeeping.models import TransactionSource

    vat_amount = invoice.vat_amount or Decimal("0.00")
    total_amount = invoice.total_amount or Decimal("0.00")
    expense_total = invoice.cost_amount_total

    validate_payable_registration(
        invoice,
        date=invoice.invoice_date,
        total_amount=total_amount,
        vat_amount=vat_amount,
        component_total=expense_total,
        labels={
            "total_not_positive": "Totalbelopp måste vara större än 0 för att bokföra fakturan.",
            "component_sum_mismatch": "Summan av kostnadsrader och moms måste vara lika med totalbelopp.",
            "period_locked": "Perioden för fakturadatumet är låst. Bokför i öppen period eller lås upp perioden.",
        },
    )

    supplier = invoice.supplier_display_name
    if invoice.cost_lines.exists():
        rows = [
            (line.expense_account, line.debit, line.credit, f"Kostnad {supplier}")
            for line in invoice.cost_lines.select_related("expense_account")
        ]
    else:
        if not invoice.expense_account_id:
            raise ValidationError("Kostnadskonto saknas för fakturan.")
        rows = [(invoice.expense_account, expense_total, Decimal("0.00"), f"Kostnad {supplier}")]

    if vat_amount > Decimal("0"):
        rows.append((invoice.vat_account, vat_amount, Decimal("0.00"), f"Ingående moms {supplier}"))
    rows.append((invoice.payable_account, Decimal("0.00"), total_amount, f"Leverantörsskuld {supplier}"))

    return post_payable_registration(
        invoice,
        user,
        date=invoice.invoice_date,
        description=f"Leverantörsfaktura {supplier}",
        reference=invoice.invoice_number,
        source=TransactionSource.SUPPLIER_INVOICE,
        rows=rows,
    )
