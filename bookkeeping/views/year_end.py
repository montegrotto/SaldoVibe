"""Årsavslutswizarden - guidad stängning av ett räkenskapsår.

En sida med stegstatus i stället för en flerstegsmaskin: förkontroller, nästa
räkenskapsår (skapas inline), bokslutsverifikationerna S1/S2, IB-bekräftelse och
helårslåsning (POST till befintlig period_lock_lock_year).
Se docs/compliance/aarsavslut/bokslutsflode-design.md.
"""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from ..compliance_policy import require_compliance_action
from ..models import Account, AccountingYear, TransactionSource
from ..period_locking import year_lock_status
from ..sie import _account_net_amounts_by_id
from ..year_end import (
    BALANCE_SHEET_CLASSES,
    annual_result,
    balance_difference,
    create_year_end_vouchers,
    precheck_errors,
    year_end_voucher,
)
from ._base import company_required


def _next_year_after(company, year):
    return AccountingYear.objects.filter(company=company, start_date__gt=year.end_date).order_by("start_date").first()


def _suggested_next_dates(year):
    next_start = year.end_date + timedelta(days=1)
    try:
        next_end = date(next_start.year + 1, next_start.month, next_start.day) - timedelta(days=1)
    except ValueError:  # 29 februari
        next_end = date(next_start.year + 1, 2, 28)
    return next_start, next_end


@login_required
@require_compliance_action("accounting_year.close")
@company_required
def year_end_close(request, company, pk):
    year = get_object_or_404(AccountingYear, pk=pk, company=company)
    next_year = _next_year_after(company, year)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_next_year":
            if next_year is None:
                next_start, next_end = _suggested_next_dates(year)
                AccountingYear.objects.create(company=company, start_date=next_start, end_date=next_end)
                messages.success(request, f"Räkenskapsåret {next_end.year} har skapats.")
        elif action == "create_vouchers":
            errors = precheck_errors(company, year, next_year)
            if errors:
                messages.error(request, "Förkontrollerna måste vara gröna innan bokslutet kan bokföras.")
            else:
                try:
                    s1, s2 = create_year_end_vouchers(company, request.user, year, next_year)
                except ValidationError as exc:
                    for message in exc.messages:
                        messages.error(request, message)
                else:
                    # Båda heter typiskt "S1" i sina respektive år - årtalet skiljer dem åt.
                    created = f"{s1.reference} ({year.name})"
                    if s2 is not None:
                        created += f" och {s2.reference} ({next_year.name})"
                    messages.success(request, f"Bokslutsverifikationerna {created} har bokförts.")
        return redirect("bookkeeping:year_end_close", pk=year.pk)

    s1 = year_end_voucher(year)
    s2 = (
        next_year.transactions.filter(
            source=TransactionSource.YEAR_END,
            date=next_year.start_date,
            correction_of__isnull=True,
            corrections__isnull=True,
        ).first()
        if next_year
        else None
    )

    opening_balances = []
    if s1 and next_year:
        # Bara balanskonton - resultatkontonas "saldon" nollställs per definition varje år
        # (resultaträkningen är per räkenskapsår) och hör inte hemma i en ingående balans.
        accounts = {
            account.pk: account
            for account in Account.objects.filter(
                company=company, is_active=True, account_class__in=BALANCE_SHEET_CLASSES
            )
        }
        amounts = _account_net_amounts_by_id(list(accounts), company=company, upto_date=year.end_date)
        opening_balances = sorted(
            ({"account": accounts[pk], "balance": amount} for pk, amount in amounts.items() if amount != 0),
            key=lambda row: row["account"].number,
        )

    next_start, next_end = _suggested_next_dates(year)
    is_ab = company.legal_form == company.LegalForm.AKTIEBOLAG
    return render(
        request,
        "bookkeeping/year_end_close.html",
        {
            "year": year,
            "next_year": next_year,
            "suggested_next_start": next_start,
            "suggested_next_end": next_end,
            "precheck_errors": precheck_errors(company, year, next_year),
            "annual_result": annual_result(year),
            "balance_difference": balance_difference(year),
            "has_transactions": year.transactions.exists(),
            "s1": s1,
            "s2": s2,
            "is_ab": is_ab,
            "result_account_number": "2099" if is_ab else "2019",
            "equity_account_number": "2091" if is_ab else "2010",
            "opening_balances": opening_balances,
            "year_locked": year_lock_status(year) == "locked",
        },
    )
