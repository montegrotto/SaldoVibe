"""Kontoplan (BAS accounts) and räkenskapsår administration."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from ..balances import build_account_balances
from ..compliance_policy import require_compliance_action
from ..forms import (
    AccountForm,
    AccountingYearForm,
)
from ..models import (
    Account,
    AccountingYear,
    Transaction,
)
from ..period_locking import year_lock_status
from ._base import company_required

logger = logging.getLogger(__name__)


@login_required
@company_required
def account_list(request, company):

    accounts = Account.objects.filter(company=company).order_by("number")
    return render(request, "bookkeeping/account_list.html", {"accounts": accounts})


@login_required
@company_required
def account_create(request, company):

    if request.method == "POST":
        form = AccountForm(request.POST, company=company)
        if form.is_valid():
            account = form.save(commit=False)
            account.company = company
            account.save()
            messages.success(request, f"Konto {account.number} har skapats.")
            return redirect("bookkeeping:account_list")
        messages.error(request, "Kunde inte skapa konto. Kontrollera formuläret och försök igen.")
    else:
        form = AccountForm(company=company)

    return render(
        request,
        "bookkeeping/account_form.html",
        {
            "form": form,
            "page_title_text": "Nytt konto",
            "submit_label": "Skapa konto",
        },
    )


@login_required
@company_required
def account_suggest_codes(request, company):

    number = request.GET.get("number", "").strip()
    name = request.GET.get("name", "").strip()
    if not (number.isdigit() and len(number) == 4):
        return JsonResponse({})
    return JsonResponse(Account.suggest_codes(number, name))


@login_required
@company_required
def account_balances_lookup(request, company):
    """Re-run build_account_balances for a caller-supplied date.

    Backs the Saldo-column preview on the verification/invoice entry forms:
    those balances are rendered once at page load for today's date, so this
    endpoint is what lets the JS refresh them when the user edits the
    bokförings-/fakturadatum field instead of leaving a stale saldo on screen.
    """
    reference_date = parse_date(request.GET.get("datum", "").strip()) or timezone.localdate()
    return JsonResponse(build_account_balances(company, reference_date))


@login_required
@company_required
def account_update(request, company, pk):

    account = get_object_or_404(Account, pk=pk, company=company)
    if request.method == "POST":
        form = AccountForm(request.POST, instance=account, company=company)
        if form.is_valid():
            form.save()
            messages.success(request, f"Konto {account.number} har uppdaterats.")
            return redirect("bookkeeping:account_list")
        messages.error(request, "Kunde inte uppdatera konto. Kontrollera formuläret och försök igen.")
    else:
        form = AccountForm(instance=account, company=company)

    return render(
        request,
        "bookkeeping/account_form.html",
        {
            "form": form,
            "account": account,
            "page_title_text": "Redigera konto",
            "submit_label": "Spara",
        },
    )


@login_required
@company_required
def accounting_year_list(request, company):

    years = list(
        AccountingYear.objects.filter(company=company)
        .annotate(verification_count=Count("transactions"))
        .order_by("-start_date")
    )
    for year in years:
        year.lock_status = year_lock_status(year)
    return render(request, "bookkeeping/accounting_year_list.html", {"years": years})


@login_required
@company_required
def accounting_year_create(request, company):

    if request.method == "POST":
        form = AccountingYearForm(request.POST, company=company)
        if form.is_valid():
            year = form.save(commit=False)
            year.company = company
            year.save()
            messages.success(request, "Räkenskapsåret har skapats.")
            return redirect("bookkeeping:accounting_year_list")
    else:
        form = AccountingYearForm(company=company)

    return render(
        request,
        "bookkeeping/accounting_year_form.html",
        {
            "form": form,
            "page_title_text": "Nytt räkenskapsår",
            "submit_label": "Skapa",
        },
    )


@login_required
@company_required
def accounting_year_update(request, company, pk):

    get_object_or_404(AccountingYear, pk=pk, company=company)
    messages.error(request, "Ett skapat räkenskapsår kan inte ändras. Du kan endast ta bort det.")
    return redirect("bookkeeping:accounting_year_list")


@login_required
@require_compliance_action("accounting_year.delete")
@company_required
def accounting_year_delete(request, company, pk):

    year = get_object_or_404(AccountingYear, pk=pk, company=company)
    transaction_count = Transaction.objects.filter(accounting_year=year).count()

    previous_year = (
        AccountingYear.objects.filter(company=company, end_date__lt=year.start_date)
        .order_by("-end_date", "-id")
        .first()
    )
    next_year = (
        AccountingYear.objects.filter(company=company, start_date__gt=year.end_date)
        .order_by("start_date", "id")
        .first()
    )

    gap_would_be_created = False
    if previous_year is not None and next_year is not None:
        expected_next_start = previous_year.end_date + timedelta(days=1)
        if next_year.start_date > expected_next_start:
            gap_would_be_created = True

    if request.method == "GET":
        return render(
            request,
            "bookkeeping/accounting_year_confirm_delete.html",
            {
                "year": year,
                "transaction_count": transaction_count,
                "gap_would_be_created": gap_would_be_created,
            },
        )

    if gap_would_be_created:
        messages.error(
            request,
            "Räkenskapsåret kan inte tas bort eftersom det skulle skapa ett glapp mellan räkenskapsår.",
        )
        return redirect("bookkeeping:accounting_year_list")

    if transaction_count:
        messages.error(
            request,
            "Räkenskapsåret kan inte tas bort eftersom det innehåller verifikationer. Arkivera företaget istället.",
        )
        return redirect("bookkeeping:accounting_year_list")

    try:
        year.delete()
    except ProtectedError:
        messages.error(
            request,
            "Räkenskapsåret kan inte tas bort eftersom det används av andra objekt.",
        )
        return redirect("bookkeeping:accounting_year_list")

    messages.success(request, "Räkenskapsåret har tagits bort.")
    return redirect("bookkeeping:accounting_year_list")
