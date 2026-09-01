"""Posting logic for customer invoices: posting an invoice into the ledger."""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from bookkeeping.payables import add_journal_entry
from bookkeeping.payables import quantize_amount as _amount

from .models import InvoiceLine


def _split_signed_amount(signed_amount, positive_side):
    amount = _amount(abs(signed_amount or Decimal("0.00")))
    if amount == Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00")
    if positive_side not in {"debit", "credit"}:
        raise ValueError("positive_side måste vara 'debit' eller 'credit'.")
    if signed_amount >= Decimal("0.00"):
        if positive_side == "debit":
            return amount, Decimal("0.00")
        return Decimal("0.00"), amount
    if positive_side == "debit":
        return Decimal("0.00"), amount
    return amount, Decimal("0.00")


def _create_signed_entry(*, transaction, account, signed_amount, positive_side, description):
    debit, credit = _split_signed_amount(signed_amount, positive_side)
    if debit == Decimal("0.00") and credit == Decimal("0.00"):
        return None

    return add_journal_entry(
        transaction=transaction,
        account=account,
        debit=debit,
        credit=credit,
        description=description,
    )


def _vat_account_number_for_rate(vat_rate):
    rate = (vat_rate or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rate == Decimal("25.00"):
        return "2611"
    if rate == Decimal("12.00"):
        return "2621"
    if rate == Decimal("6.00"):
        return "2631"
    return None


def bookkeep_invoice(invoice, user):
    if invoice.is_booked:
        return invoice.booked_transaction

    from django.db import transaction as db_transaction
    from django.utils import timezone

    from bookkeeping.models import Transaction, TransactionSource
    from bookkeeping.period_locking import is_date_locked

    lines = list(invoice.lines.select_related("article", "article__income_account").all())
    item_lines = [line for line in lines if line.line_type == InvoiceLine.LINE_TYPE_ITEM]
    if not item_lines:
        raise ValidationError("Fakturan måste innehålla minst en artikelrad för att bokföras.")

    if is_date_locked(invoice.company, invoice.invoice_date):
        raise ValidationError("Perioden för fakturadatumet är låst. Bokför i öppen period eller lås upp perioden.")

    accounting_year = invoice.company.accounting_years.filter(
        start_date__lte=invoice.invoice_date,
        end_date__gte=invoice.invoice_date,
    ).first()
    if accounting_year is None:
        raise ValidationError("Inget räkenskapsår matchar fakturadatumet.")

    receivable_account = invoice.company.accounts.filter(is_active=True, number="1510").first()
    if receivable_account is None:
        raise ValidationError("Standardkonto 1510 saknas i kontoplanen.")

    revenue_amounts = {}
    vat_amounts = {}
    total_ex_vat = Decimal("0.00")
    total_vat = Decimal("0.00")

    for line in item_lines:
        if line.article is None:
            raise ValidationError("Varje fakturarad måste vara kopplad till en artikel för att kunna bokföras.")
        if line.article.income_account is None:
            raise ValidationError(f"Artikeln '{line.article.name}' saknar intäktskonto.")

        line_ex_vat = _amount(line.line_total_ex_vat)
        line_vat = _amount(line_ex_vat * ((line.vat_rate or Decimal("0.00")) / Decimal("100")))

        revenue_account = line.article.income_account
        revenue_amounts[revenue_account.id] = {
            "account": revenue_account,
            "amount": revenue_amounts.get(revenue_account.id, {"amount": Decimal("0.00")})["amount"] + line_ex_vat,
        }

        if line_vat != Decimal("0.00"):
            vat_account_number = _vat_account_number_for_rate(line.vat_rate)
            if not vat_account_number:
                raise ValidationError(f"Moms {line.vat_rate}% stöds inte automatiskt. Endast 25%, 12% och 6% stöds.")
            vat_account = invoice.company.accounts.filter(is_active=True, number=vat_account_number).first()
            if vat_account is None:
                raise ValidationError(f"Momskonto {vat_account_number} saknas i kontoplanen.")
            vat_amounts[vat_account.id] = {
                "account": vat_account,
                "amount": vat_amounts.get(vat_account.id, {"amount": Decimal("0.00")})["amount"] + line_vat,
            }

        total_ex_vat += line_ex_vat
        total_vat += line_vat

    total_amount = _amount(total_ex_vat + total_vat)

    with db_transaction.atomic():
        # Radlås + omkontroll: två samtidiga (eller dubbelklickade) bokföringar får en verifikation, inte två.
        locked = type(invoice).objects.select_for_update().get(pk=invoice.pk)
        if locked.is_booked:
            return locked.booked_transaction
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date=invoice.invoice_date,
            description=f"Kundfaktura {invoice.invoice_number}",
            reference=invoice.invoice_number,
            created_by=user,
            source=TransactionSource.SALES_INVOICE,
        )

        _create_signed_entry(
            transaction=txn,
            account=receivable_account,
            signed_amount=total_amount,
            positive_side="debit",
            description=f"Kundfordran {invoice.customer.name}",
        )

        for item in revenue_amounts.values():
            _create_signed_entry(
                transaction=txn,
                account=item["account"],
                signed_amount=item["amount"],
                positive_side="credit",
                description=f"Försäljning {invoice.customer.name}",
            )

        for item in vat_amounts.values():
            _create_signed_entry(
                transaction=txn,
                account=item["account"],
                signed_amount=item["amount"],
                positive_side="credit",
                description=f"Utgående moms {invoice.customer.name}",
            )

        txn.validate_balanced()

        invoice.accounting_year = accounting_year
        invoice.receivable_account = receivable_account
        invoice.is_booked = True
        invoice.booked_at = timezone.now()
        invoice.booked_transaction = txn
        invoice.save(
            update_fields=[
                "accounting_year",
                "receivable_account",
                "is_booked",
                "booked_at",
                "booked_transaction",
            ]
        )

        return txn
