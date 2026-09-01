"""Periodlås per BFNAR 2013:2 - locking, reopening and relocking ranges."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..compliance_policy import require_compliance_action
from ..forms import (
    PeriodLockForm,
    PeriodLockRelockForm,
    PeriodLockReopenForm,
)
from ..models import (
    AccountingYear,
    PeriodLock,
)
from ..period_locking import is_date_locked, suggest_monthly_periods
from ..reports import default_accounting_year
from ._base import company_required

logger = logging.getLogger(__name__)


def _selected_accounting_year_for_locks(request, company):
    years = AccountingYear.objects.filter(company=company).order_by("-start_date", "-id")
    requested_year_id = request.GET.get("year") or request.POST.get("accounting_year")
    if requested_year_id:
        selected_year = years.filter(pk=requested_year_id).first()
        if selected_year is not None:
            return selected_year, years
    return default_accounting_year(years), years


@login_required
@require_compliance_action("period_lock.manage")
@company_required
def period_lock_list(request, company):

    selected_year, years = _selected_accounting_year_for_locks(request, company)
    if selected_year is None:
        messages.info(request, "Skapa ett räkenskapsår innan du kan låsa perioder.")
        return redirect("bookkeeping:accounting_year_list")

    locks = PeriodLock.objects.filter(accounting_year=selected_year).select_related("locked_by", "reopened_by")
    year_is_fully_locked = is_date_locked(company, selected_year.start_date) and is_date_locked(
        company, selected_year.end_date
    )

    monthly_suggestions = suggest_monthly_periods(selected_year)

    return render(
        request,
        "bookkeeping/period_lock_list.html",
        {
            "years": years,
            "selected_year": selected_year,
            "locks": locks,
            "monthly_suggestions": monthly_suggestions,
            "year_is_fully_locked": year_is_fully_locked,
            "reopen_form": PeriodLockReopenForm(),
            "relock_form": PeriodLockRelockForm(),
        },
    )


@login_required
@require_compliance_action("period_lock.manage")
@company_required
def period_lock_create(request, company):
    """Lock a single calendar month, as submitted by one of the "Föreslagna
    månader" buttons on period_lock_list - there is no UI for locking an
    arbitrary custom date range, only whole fiscal years or whole months."""
    selected_year, _years = _selected_accounting_year_for_locks(request, company)
    if selected_year is None:
        return redirect("bookkeeping:accounting_year_list")

    if request.method == "POST":
        form = PeriodLockForm(request.POST, accounting_year=selected_year)
        if form.is_valid():
            lock = form.save(commit=False)
            lock.company = company
            lock.accounting_year = selected_year
            lock.is_locked = True
            lock.locked_by = request.user
            lock.save()
            messages.success(request, "Perioden har låsts.")
        else:
            error_messages = [str(err) for err in form.non_field_errors()]
            for field_name, field_errors in form.errors.items():
                if field_name == "__all__":
                    continue
                label = form.fields[field_name].label if field_name in form.fields else field_name
                error_messages.extend([f"{label}: {err}" for err in field_errors])
            detail = " " + " ".join(error_messages) if error_messages else ""
            messages.error(request, f"Kunde inte låsa perioden.{detail}")

    return redirect(f"{reverse('bookkeeping:period_lock_list')}?year={selected_year.pk}")


@login_required
@require_compliance_action("period_lock.manage")
@company_required
def period_lock_lock_year(request, company, pk):

    year = get_object_or_404(AccountingYear, pk=pk, company=company)

    if request.method == "POST":
        existing = PeriodLock.objects.filter(
            accounting_year=year, period_start=year.start_date, period_end=year.end_date
        ).first()
        if existing is not None:
            if not existing.is_locked:
                existing.is_locked = True
                existing.reason = "Helårslåsning (bokslut)"
                existing.locked_by = request.user
                existing.reopened_reason = ""
                existing.reopened_by = None
                existing.reopened_at = None
                existing.save()
                messages.success(request, "Hela räkenskapsåret är låst.")
            else:
                messages.info(request, "Hela räkenskapsåret är redan låst.")
        else:
            PeriodLock.objects.create(
                company=company,
                accounting_year=year,
                period_start=year.start_date,
                period_end=year.end_date,
                reason="Helårslåsning (bokslut)",
                locked_by=request.user,
            )
            messages.success(request, "Hela räkenskapsåret är låst.")

    return redirect(f"{reverse('bookkeeping:period_lock_list')}?year={year.pk}")


@login_required
@require_compliance_action("period_lock.manage")
@company_required
def period_lock_reopen(request, company, pk):

    lock = get_object_or_404(PeriodLock, pk=pk, company=company)

    if request.method == "POST":
        form = PeriodLockReopenForm(request.POST)
        if form.is_valid():
            lock.is_locked = False
            lock.reopened_reason = form.cleaned_data["reopened_reason"]
            lock.reopened_by = request.user
            lock.reopened_at = timezone.now()
            lock.save()
            messages.success(request, "Perioden har låsts upp.")
        else:
            messages.error(request, "Ange en orsak för att låsa upp perioden.")

    return redirect(f"{reverse('bookkeeping:period_lock_list')}?year={lock.accounting_year_id}")


@login_required
@require_compliance_action("period_lock.manage")
@company_required
def period_lock_relock(request, company, pk):

    lock = get_object_or_404(PeriodLock, pk=pk, company=company)

    if request.method == "POST":
        form = PeriodLockRelockForm(request.POST)
        if form.is_valid():
            lock.is_locked = True
            lock.reason = form.cleaned_data["reason"]
            lock.locked_by = request.user
            lock.reopened_reason = ""
            lock.reopened_by = None
            lock.reopened_at = None
            lock.save()
            messages.success(request, "Perioden är låst igen.")
        else:
            messages.error(request, "Ange en orsak för att låsa perioden igen.")

    return redirect(f"{reverse('bookkeeping:period_lock_list')}?year={lock.accounting_year_id}")
