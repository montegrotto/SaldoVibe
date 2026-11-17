"""Manuella verifikationer: the one write path behind both the web's Ny verifikation and the
API (api.views.vouchers), so the two cannot disagree on which checks a voucher passes.
Row validation and the balance check live in TransactionForm/JournalEntryFormSet."""

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from .models import AccountingYear
from .period_locking import is_date_locked


def check_manual_voucher(company, form):
    """The checks the forms can't make: open period and exactly one räkenskapsår for the date.
    Returns that year; raises ValidationError otherwise."""
    txn_date = form.cleaned_data["date"]
    if is_date_locked(company, txn_date):
        raise ValidationError(
            "Den valda perioden är låst. Skapa en korrigeringsverifikation i öppen period eller lås upp perioden."
        )
    years = list(AccountingYear.objects.filter(company=company, start_date__lte=txn_date, end_date__gte=txn_date)[:2])
    if not years:
        raise ValidationError("Inget räkenskapsår matchar valt datum.")
    if len(years) > 1:
        raise ValidationError("Flera räkenskapsår matchar valt datum. Kontrollera räkenskapsåren.")
    return years[0]


def save_manual_voucher(*, company, user, form, formset, attachments=None):
    """Book a valid TransactionForm + JournalEntryFormSet pair; returns the Transaction."""
    year = check_manual_voucher(company, form)
    with db_transaction.atomic():
        txn = form.save(commit=False)
        txn.accounting_year = year
        txn.created_by = user
        txn.save()
        formset.instance = txn
        formset.save()
        txn.validate_balanced()
        if attachments is not None and attachments.exists():
            txn.attachments.add(*attachments)
    return txn
