import calendar
from datetime import date, timedelta

from .models import PeriodLock


def is_date_locked(company, target_date):
    return PeriodLock.objects.filter(
        company=company,
        is_locked=True,
        period_start__lte=target_date,
        period_end__gte=target_date,
    ).exists()


def is_range_locked(company, period_start, period_end):
    return PeriodLock.objects.filter(
        company=company,
        is_locked=True,
        period_start__lte=period_end,
        period_end__gte=period_start,
    ).exists()


def year_lock_status(accounting_year):
    """Return "locked" if active locks cover the whole year without gaps,
    "partial" if some but not all of the year is locked, otherwise "open"."""
    intervals = (
        PeriodLock.objects.filter(accounting_year=accounting_year, is_locked=True)
        .order_by("period_start")
        .values_list("period_start", "period_end")
    )
    if not intervals:
        return "open"

    cursor = accounting_year.start_date
    for period_start, period_end in intervals:
        if period_start > cursor:
            break
        cursor = max(cursor, period_end + timedelta(days=1))

    if cursor > accounting_year.end_date:
        return "locked"
    return "partial"


def suggest_monthly_periods(accounting_year, today=None):
    """Return each elapsed calendar month within the accounting year, so a
    finance_admin can progressively lock closed months as they pass, per
    Bokföringsnämndens krav on löpande avstämning/periodavslut (BFNAR 2013:2).

    Only months that have fully passed (period_end < today) are suggested -
    the current, still-open month is deliberately left out.
    """
    if today is None:
        today = date.today()

    periods = []
    cursor = accounting_year.start_date
    while cursor <= accounting_year.end_date:
        days_in_month = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, days_in_month)
        period_end = min(month_end, accounting_year.end_date)

        if period_end < today:
            periods.append(
                {
                    "start_date": cursor,
                    "end_date": period_end,
                    "is_locked": is_range_locked(accounting_year.company, cursor, period_end),
                }
            )

        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    return periods
