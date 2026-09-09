"""Semesterlöneskuld vid bokslut (bokslutsmetoden).

Löpande bokförs semesterlön/-tillägg som vanlig lönekostnad när den betalas ut. Vid
bokslut justeras skulden på 2920 (upplupna semesterlöner) och 2941 (upplupna sociala
avgifter) så att den motsvarar de anställdas kvarvarande semesterdagar:

  skuld per anställd = kvarvarande dagar × 5,03 % × månadslön × sysselsättningsgrad
  (4,6 % semesterlön per dag enligt sammalöneregeln + 0,43 % semestertillägg)
  sociala avgifter   = skuld × arbetsgivaravgiftsprocenten från senaste lönepost (annars 31,42 %)

Bokningen är skillnaden mot befintligt saldo, så steget kan köras om utan dubbelbokning.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Sum

from bookkeeping.models import Account, JournalEntry, Transaction, TransactionSource
from bookkeeping.period_locking import is_date_locked

from .models import VACATION_SUPPLEMENT_RATE, Employee, SalaryRecord, quantize_money

VACATION_PAY_RATE_PER_DAY = Decimal("0.046") + VACATION_SUPPLEMENT_RATE  # 5,03 %
DEFAULT_EMPLOYER_CONTRIBUTION_RATE = Decimal("31.42")

ACCOUNT_NUMBERS = {
    "liability": "2920",
    "contribution_liability": "2941",
    "cost": "7290",
    "contribution_cost": "7519",
}

ZERO = Decimal("0.00")


def vacation_liability_rows(company):
    """En rad per aktiv anställd med semesterdagar kvar."""
    rows = []
    for employee in Employee.objects.filter(company=company, is_active=True, vacation_days_balance__gt=0):
        latest = SalaryRecord.objects.filter(employee=employee).order_by("-payroll_run__payment_date", "-id").first()
        rate = latest.employer_contribution_rate if latest else DEFAULT_EMPLOYER_CONTRIBUTION_RATE
        monthly = (employee.monthly_salary or ZERO) * (employee.employment_rate or ZERO) / 100
        amount = quantize_money(employee.vacation_days_balance * VACATION_PAY_RATE_PER_DAY * monthly)
        rows.append(
            {
                "employee": employee,
                "days": employee.vacation_days_balance,
                "monthly_salary": quantize_money(monthly),
                "amount": amount,
                "contribution_rate": rate,
                "contribution": quantize_money(amount * rate / 100),
            }
        )
    return rows


def _credit_balance(company, number, upto_date):
    totals = JournalEntry.objects.filter(
        account__company=company, account__number=number, transaction__date__lte=upto_date
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (totals["c"] or ZERO) - (totals["d"] or ZERO)


def vacation_liability_status(company, year):
    """Beräknad skuld, bokförd skuld per årets sista dag och skillnaden att bokföra."""
    rows = vacation_liability_rows(company)
    target = sum((row["amount"] for row in rows), ZERO)
    target_contribution = sum((row["contribution"] for row in rows), ZERO)
    booked = _credit_balance(company, ACCOUNT_NUMBERS["liability"], year.end_date)
    booked_contribution = _credit_balance(company, ACCOUNT_NUMBERS["contribution_liability"], year.end_date)
    return {
        "rows": rows,
        "target": target,
        "target_contribution": target_contribution,
        "booked": booked,
        "booked_contribution": booked_contribution,
        "diff": target - booked,
        "diff_contribution": target_contribution - booked_contribution,
    }


def _adjust(txn, cost_account, liability_account, diff, description):
    if diff > 0:
        JournalEntry.objects.create(transaction=txn, account=cost_account, debit=diff, description=description)
        JournalEntry.objects.create(transaction=txn, account=liability_account, credit=diff, description=description)
    elif diff < 0:
        JournalEntry.objects.create(transaction=txn, account=liability_account, debit=-diff, description=description)
        JournalEntry.objects.create(transaction=txn, account=cost_account, credit=-diff, description=description)


def book_vacation_liability(company, user, year):
    """Bokför justeringen av semesterlöneskulden på årets sista dag. Returnerar
    verifikationen, eller None om skulden redan stämmer."""
    status = vacation_liability_status(company, year)
    if status["diff"] == 0 and status["diff_contribution"] == 0:
        return None
    if is_date_locked(company, year.end_date):
        raise ValidationError("Årets sista dag ligger i en låst period – semesterlöneskulden kan inte bokföras.")

    accounts = {
        key: Account.objects.filter(company=company, number=number, is_active=True).first()
        for key, number in ACCOUNT_NUMBERS.items()
    }
    missing = sorted(ACCOUNT_NUMBERS[key] for key, account in accounts.items() if account is None)
    if missing:
        raise ValidationError("Saknar konton för semesterlöneskuld: " + ", ".join(missing) + ".")

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            accounting_year=year,
            date=year.end_date,
            description=f"Semesterlöneskuld per {year.end_date}",
            source=TransactionSource.PAYROLL,
            created_by=user,
        )
        _adjust(txn, accounts["cost"], accounts["liability"], status["diff"], "Förändring semesterlöneskuld")
        _adjust(
            txn,
            accounts["contribution_cost"],
            accounts["contribution_liability"],
            status["diff_contribution"],
            "Sociala avgifter på semesterlöneskuld",
        )
        txn.validate_balanced()
    return txn
