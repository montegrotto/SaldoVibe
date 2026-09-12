"""Balansräkning, resultaträkning, huvudbok and reskontra, on screen and as PDF."""

import logging
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import BudgetForm
from ..models import AccountingYear
from ..pdf import company_logo_size, render_pdf_response
from ..reports import (
    build_balance_sheet_context,
    build_general_ledger_context,
    build_income_forecast_context,
    build_income_statement_context,
    build_reskontra_context,
)
from ._base import company_required

logger = logging.getLogger(__name__)


@login_required
@company_required
def balance_sheet(request, company):
    """Balansräkning – accounts class 1 (assets) and class 2 (equity/liabilities)."""

    context = build_balance_sheet_context(request, company)
    return render(request, "bookkeeping/balance_sheet.html", context)


@login_required
@company_required
def balance_sheet_pdf(request, company):

    context = build_balance_sheet_context(request, company)
    context["company"] = company
    context["logo_size"] = company_logo_size(company)
    year_label = context["selected_year"].name if context.get("selected_year") else ""
    filename = f"balansräkning_{year_label}.pdf" if year_label else "balansräkning.pdf"
    return render_pdf_response("bookkeeping/balance_sheet_pdf.html", context, filename)


@login_required
@company_required
def income_statement(request, company):
    """Resultaträkning in Swedish grouped format."""

    context = build_income_statement_context(request, company)
    return render(request, "bookkeeping/income_statement.html", context)


@login_required
@company_required
def income_statement_pdf(request, company):

    context = build_income_statement_context(request, company)
    context["company"] = company
    context["logo_size"] = company_logo_size(company)
    year_label = context["selected_year"].name if context.get("selected_year") else ""
    filename = f"resultaträkning_{year_label}.pdf" if year_label else "resultaträkning.pdf"
    return render_pdf_response("bookkeeping/income_statement_pdf.html", context, filename)


@login_required
@company_required
def income_forecast(request, company):
    """Resultatprognos – utfall t.o.m. vald månad plus ett eget belopp för resten av året."""

    context = build_income_forecast_context(request, company)
    return render(request, "bookkeeping/income_forecast.html", context)


@login_required
@company_required
def general_ledger(request, company):
    """Huvudbok – alla konton med ingående saldo, årets rader och utgående saldo."""

    context = build_general_ledger_context(request, company)
    return render(request, "bookkeeping/general_ledger.html", context)


@login_required
@company_required
def general_ledger_pdf(request, company):

    context = build_general_ledger_context(request, company)
    context["company"] = company
    context["logo_size"] = company_logo_size(company)
    year_label = context["selected_year"].name if context.get("selected_year") else ""
    filename = f"huvudbok_{year_label}.pdf" if year_label else "huvudbok.pdf"
    return render_pdf_response("bookkeeping/general_ledger_pdf.html", context, filename)


def _reskontra_report_date(request):
    """?date=YYYY-MM-DD from the date picker; anything else means today."""
    try:
        return date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return None


@login_required
@company_required
def reskontra(request, company):
    """Kund- och leverantörsreskontra med åldersanalys och avstämning."""

    context = build_reskontra_context(company, report_date=_reskontra_report_date(request))
    return render(request, "bookkeeping/reskontra.html", context)


@login_required
@company_required
def reskontra_pdf(request, company):

    context = build_reskontra_context(company, report_date=_reskontra_report_date(request))
    context["company"] = company
    context["logo_size"] = company_logo_size(company)
    filename = f"reskontra_{context['report_date'].isoformat()}.pdf"
    return render_pdf_response("bookkeeping/reskontra_pdf.html", context, filename)


def _budget_extra_account_ids(raw):
    """Konton som lagts till manuellt på budgetsidan, som kommaseparerade id:n. BudgetForm
    kontrollerar sedan att de hör till företaget och är resultatkonton."""
    return [int(part) for part in (raw or "").split(",") if part.strip().isdigit()]


@login_required
@company_required
def budget_edit(request, company, pk):
    """Edit every budgeted month/account amount for one accounting year in a single page -
    a grid, not one-row-at-a-time CRUD."""

    accounting_year = get_object_or_404(AccountingYear, pk=pk, company=company)

    if request.method == "POST":
        extra_account_ids = _budget_extra_account_ids(request.POST.get("extra_accounts")) + (
            _budget_extra_account_ids(request.POST.get("add_account"))
        )
        form = BudgetForm(
            request.POST, company=company, accounting_year=accounting_year, extra_account_ids=extra_account_ids
        )
        if form.is_valid():
            form.save()
            messages.success(request, f"Budgeten för {accounting_year.name} har sparats.")
            if request.POST.get("action") == "add":
                # Spara först, visa sedan sidan med det nya kontot - annars försvinner
                # allt som redan skrivits in i rutnätet när sidan laddas om.
                keep = ",".join(str(account_id) for account_id in form.extra_account_ids)
                return redirect(f"{request.path}?konto={keep}")
            return redirect("bookkeeping:income_statement")
        messages.error(request, "Kunde inte spara budgeten. Kontrollera formuläret och försök igen.")
    else:
        form = BudgetForm(
            company=company,
            accounting_year=accounting_year,
            extra_account_ids=_budget_extra_account_ids(request.GET.get("konto")),
        )

    return render(
        request,
        "bookkeeping/budget_form.html",
        {"form": form, "accounting_year": accounting_year},
    )
