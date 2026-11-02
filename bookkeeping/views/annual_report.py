"""Årsredovisning (K2, aktiebolag): fritexter + förhandsgranskning, och PDF.

Siffrorna kommer från bookkeeping/annual_report.py; sidan sparar bara det som en
människa måste skriva (förvaltningsberättelse, resultatdisposition, underskrifter).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ..annual_report import build_annual_report_context
from ..forms import AnnualReportForm
from ..models import AccountingYear, AnnualReport, Company
from ..pdf import company_logo_size, render_pdf_response
from ._base import company_required


def _load(request, company, pk):
    year = get_object_or_404(AccountingYear, pk=pk, company=company)
    if company.legal_form != Company.LegalForm.AKTIEBOLAG:
        messages.error(
            request,
            "Årsredovisning enligt K2 finns bara för aktiebolag – sätt bolagsform i företagsinställningarna.",
        )
        return year, None
    report = AnnualReport.objects.filter(accounting_year=year).first() or AnnualReport(accounting_year=year)
    return year, report


@login_required
@company_required
def annual_report(request, company, pk):
    year, report = _load(request, company, pk)
    if report is None:
        return redirect("bookkeeping:accounting_year_list")

    form = AnnualReportForm(request.POST or None, instance=report)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Årsredovisningens uppgifter har sparats.")
        return redirect("bookkeeping:annual_report", pk=year.pk)

    context = build_annual_report_context(company, year, report)
    context["form"] = form
    return render(request, "bookkeeping/annual_report.html", context)


@login_required
@company_required
def annual_report_pdf(request, company, pk):
    year, report = _load(request, company, pk)
    if report is None:
        return redirect("bookkeeping:accounting_year_list")

    context = build_annual_report_context(company, year, report)
    context["logo_size"] = company_logo_size(company)
    return render_pdf_response("bookkeeping/annual_report_pdf.html", context, f"arsredovisning_{year.name}.pdf")
