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


def _skatteverket_due_dates(today, end_date):
    year, month = today.year, today.month
    while (due := skatteverket_due_date(year, month)) <= end_date:
        if due >= today:
            yield due
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _account_balance(company, numbers, before=None):
    """Debit minus credit across the given accounts, all years (opening balances are booked)."""
    entries = JournalEntry.objects.filter(transaction__accounting_year__company=company, account__number__in=numbers)
    if before:
        entries = entries.filter(transaction__date__lt=before)
    totals = entries.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    return (totals["debit"] or Decimal("0.00")) - (totals["credit"] or Decimal("0.00"))


def tax_account_payment_items(company, today, end_date):
    """Expected skattekonto debits as (date, label, amount) from today through end_date.

    Four sources: the unpaid VAT on 2650 (due at the filing deadline of the latest closed
    period), withheld tax on 2710 and employer contributions on 2731 (salary paid in month M is due
    on the Skatteverket due date in M+1), and the monthly F-skatt from company settings.
    """
    from vat.models import VatCloseSnapshot

    items = []

    vat_due = -_account_balance(company, ["2650"])
    if vat_due:
        latest_close = VatCloseSnapshot.objects.filter(company=company).order_by("-period_end").first()
        due = vat_filing_deadline(latest_close.period_end) if latest_close else today
        items.append((max(due, today), "Moms", vat_due))

    for number, label in (("2710", "Avdragen skatt"), ("2731", "Arbetsgivaravgift")):
        # Each due date takes what was booked before its month and isn't already allotted to an
        # earlier one, so a booked next-month salary doesn't double the upcoming debit.
        allotted = Decimal("0.00")
        for due in _skatteverket_due_dates(today, end_date):
            outstanding = -_account_balance(company, [number], before=due.replace(day=1))
            if outstanding > allotted:
                items.append((due, label, outstanding - allotted))
                allotted = outstanding

    if company.preliminary_tax_monthly:
        for due in _skatteverket_due_dates(today, end_date):
            items.append((due, "Debiterad preliminärskatt", company.preliminary_tax_monthly))

    return sorted((item for item in items if item[0] <= end_date), key=lambda item: item[0])


def tax_account_payment_events(company, today, end_date):
    """Expected skattekonto debits summed per date (date -> amount) for the liquidity forecast."""
    events = defaultdict(lambda: Decimal("0.00"))
    for day, _label, amount in tax_account_payment_items(company, today, end_date):
        events[day] += amount
    return dict(events)


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
    items = tax_account_payment_items(company, today, today + timedelta(days=SHORTFALL_HORIZON_DAYS))
    events = defaultdict(lambda: Decimal("0.00"))
    for day, _label, amount in items:
        events[day] += amount
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
        "next_debit_items": [(label, amount) for day, label, amount in items if day == next_debit_date],
        "shortfall_date": shortfall[0] if shortfall else None,
        "shortfall_amount": shortfall[1] if shortfall else None,
    }
