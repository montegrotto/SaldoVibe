"""Periodiseringar: split a cost or revenue over N months into that many vouchers.

Bokförs direkt (no background job), same pattern as
``invoicing.RecurringInvoice.generate_invoice`` - every voucher is created synchronously
in the request. Unlike the recurring-invoice flow (one voucher per click, over time), a
periodisering creates all N vouchers in a single call, so every one of those N dates has
to be checked against period locks *before* anything is written - see ``suggest_monthly_periods``
in period_locking.py for the same "walk calendar months with ``calendar.monthrange``" idea.

v1 is deliberately just the creation of the N vouchers - no persisted "periodization" record
and no automatic reversal/dissolution tracking (see docs/feature-roadmap.md item 2).
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from .models import AccountClass, AccountingYear, JournalEntry, Transaction, TransactionSource
from .period_locking import is_date_locked


def voucher_dates_for_months(start_date, months):
    """Return the last day of each of ``months`` consecutive calendar months, starting
    with the month ``start_date`` falls in. The day-of-month on ``start_date`` itself is
    ignored - only its year/month anchor the sequence."""

    dates = []
    year, month = start_date.year, start_date.month
    for offset in range(months):
        total_month = month - 1 + offset
        target_year = year + total_month // 12
        target_month = total_month % 12 + 1
        last_day = calendar.monthrange(target_year, target_month)[1]
        dates.append(date(target_year, target_month, last_day))
    return dates


def split_amount(total_amount, months):
    """Split ``total_amount`` into ``months`` period amounts that sum exactly back to
    ``total_amount``. Every period but the last gets the same rounded share; the last
    absorbs the rounding remainder."""

    per_period = (total_amount / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amounts = [per_period] * (months - 1)
    amounts.append(total_amount - per_period * (months - 1))
    return amounts


def create_periodization_vouchers(company, user, *, description, amount, account, counter_account, months, start_date):
    """Create ``months`` vouchers that spread ``amount`` for ``account`` over consecutive
    calendar months, booked against ``counter_account`` (a 17xx prepaid-expense or 29xx
    accrued/deferred-revenue account).

    Raises ``ValidationError`` (with one message per failing period) and creates nothing if
    any of the N dates falls in a locked period or doesn't match exactly one accounting year -
    this is all-or-nothing, not a partial "create the ones that fit" run.
    """

    dates = voucher_dates_for_months(start_date, months)

    errors = []
    accounting_year_by_date = {}
    for voucher_date in dates:
        if is_date_locked(company, voucher_date):
            errors.append(f"Perioden {voucher_date:%Y-%m} är låst.")
            continue
        matching_years = AccountingYear.objects.filter(
            company=company,
            start_date__lte=voucher_date,
            end_date__gte=voucher_date,
        ).order_by("-start_date", "-id")
        count = matching_years.count()
        if count == 0:
            errors.append(f"Inget räkenskapsår matchar {voucher_date:%Y-%m}.")
        elif count > 1:
            errors.append(f"Flera räkenskapsår matchar {voucher_date:%Y-%m}.")
        else:
            accounting_year_by_date[voucher_date] = matching_years.first()

    if errors:
        raise ValidationError(errors)

    is_revenue = account.account_class == AccountClass.REVENUE
    amounts = split_amount(amount, months)

    created = []
    with db_transaction.atomic():
        for index, (voucher_date, period_amount) in enumerate(zip(dates, amounts), start=1):
            txn = Transaction.objects.create(
                accounting_year=accounting_year_by_date[voucher_date],
                date=voucher_date,
                description=f"{description} ({index}/{months})",
                source=TransactionSource.MANUAL,
                created_by=user,
            )
            # Revenue being deferred: counter_account (29xx) carries the liability down,
            # the revenue account is credited as it's recognized. Cost being prepaid: the
            # cost account is debited as it's recognized, counter_account (17xx) carries
            # the asset down.
            if is_revenue:
                JournalEntry.objects.create(transaction=txn, account=counter_account, debit=period_amount)
                JournalEntry.objects.create(transaction=txn, account=account, credit=period_amount)
            else:
                JournalEntry.objects.create(transaction=txn, account=account, debit=period_amount)
                JournalEntry.objects.create(transaction=txn, account=counter_account, credit=period_amount)
            txn.validate_balanced()
            created.append(txn)

    return created
