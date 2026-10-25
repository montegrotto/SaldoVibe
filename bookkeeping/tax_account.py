"""Skattekontot (1630): förväntade dragningar och uppföljning av saldot.

Delas av startsidans likviditetsprognos och skattekontoruta samt notiskällorna i
notifications.py (underskott, gammal import).
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from .models import JournalEntry
from .notifications import skatteverket_due_date, vat_filing_deadline

TAX_ACCOUNT_NUMBER = "1630"
# Hur långt fram startsidan och notisen letar efter en dragning som saldot inte täcker.
SHORTFALL_HORIZON_DAYS = 30


def next_skatteverket_due_date(today):
    due = skatteverket_due_date(today.year, today.month)
    if due >= today:
        return due
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return skatteverket_due_date(year, month)


def _account_balance(company, numbers):
    """Debit minus credit across the given accounts, all years (opening balances are booked)."""
    totals = JournalEntry.objects.filter(
        transaction__accounting_year__company=company, account__number__in=numbers
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (totals["debit"] or Decimal("0.00")) - (totals["credit"] or Decimal("0.00"))


def tax_account_payment_events(company, today, end_date):
    """Expected skattekonto debits (date -> amount) from today through end_date.

    Three sources: the unpaid VAT on 2650 (due at the filing deadline of the latest closed
    period), unpaid employer contributions and withheld tax on 2731/2710 (due the next
    Skatteverket due date), and the monthly F-skatt from company settings.
    """
    from vat.models import VatCloseSnapshot

    events = defaultdict(lambda: Decimal("0.00"))

    vat_due = -_account_balance(company, ["2650"])
    if vat_due:
        latest_close = VatCloseSnapshot.objects.filter(company=company).order_by("-period_end").first()
        due = vat_filing_deadline(latest_close.period_end) if latest_close else today
        events[max(due, today)] += vat_due

    payroll_due = -_account_balance(company, ["2710", "2731"])
    if payroll_due:
        events[next_skatteverket_due_date(today)] += payroll_due

    if company.preliminary_tax_monthly:
        year, month = today.year, today.month
        while True:
            due = skatteverket_due_date(year, month)
            if due > end_date:
                break
            if due >= today:
                events[due] += company.preliminary_tax_monthly
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return {day: amount for day, amount in events.items() if day <= end_date}


def first_shortfall(balance, events):
    """(date, missing amount) for the first expected debit the running balance can't cover, else None."""
    running = balance
    for day in sorted(events):
        running -= events[day]
        if running < 0:
            return day, -running
    return None


def tax_account_summary(company, today):
    """What the start page shows about skattekontot; also feeds the shortfall notice."""
    from banking.models import BankAccountType, BankImport, BankTransaction

    booked_balance = _account_balance(company, [TAX_ACCOUNT_NUMBER])
    latest_imported = (
        BankTransaction.objects.filter(
            company=company, bank_account__account_type=BankAccountType.TAX, balance__isnull=False
        )
        .order_by("-date", "-id")
        .first()
    )
    latest_import = (
        BankImport.objects.filter(company=company, bank_account__account_type=BankAccountType.TAX)
        .order_by("-imported_at")
        .first()
    )
    events = tax_account_payment_events(company, today, today + timedelta(days=SHORTFALL_HORIZON_DAYS))
    next_debit_date = min(events) if events else None
    shortfall = first_shortfall(booked_balance, events)
    return {
        "booked_balance": booked_balance,
        "imported_balance": latest_imported.balance if latest_imported else None,
        "imported_balance_date": latest_imported.date if latest_imported else None,
        "difference": (booked_balance - latest_imported.balance) if latest_imported else None,
        "last_import_at": latest_import.imported_at if latest_import else None,
        "next_debit_date": next_debit_date,
        "next_debit_amount": events[next_debit_date] if next_debit_date else Decimal("0.00"),
        "shortfall_date": shortfall[0] if shortfall else None,
        "shortfall_amount": shortfall[1] if shortfall else None,
    }
