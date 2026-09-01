"""Bokföringsexport: picker/status page + background job start/download/delete
(bookkeeping/export_bundle.py)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..compliance_policy import require_compliance_action
from ..export_bundle import (
    bank_transaction_counts,
    document_bucket_querysets,
    overlapping_accounting_years,
    reports_counts,
    start_export_job,
)
from ..forms import ExportBundleForm
from ..models import AccountingYear, ExportJob
from ._base import company_required


@login_required
@company_required
def export_bundle_form(request, company):
    ExportJob.objects.filter(company=company, status=ExportJob.Status.COMPLETED, seen_at__isnull=True).update(
        seen_at=timezone.now()
    )

    years = AccountingYear.objects.filter(company=company).order_by("start_date")
    earliest_year = years.first()
    latest_year = years.last()
    default_start = earliest_year.start_date if earliest_year else timezone.localdate().replace(month=1, day=1)
    default_end = latest_year.end_date if latest_year else timezone.localdate()

    if request.GET:
        form = ExportBundleForm(request.GET)
    else:
        form = ExportBundleForm(initial={"start_date": default_start, "end_date": default_end})

    preview = None
    if form.is_valid():
        start_date = form.cleaned_data["start_date"]
        end_date = form.cleaned_data["end_date"]
        booked_qs, not_booked_qs = document_bucket_querysets(company, start_date, end_date)
        preview = {
            "start_date": start_date,
            "end_date": end_date,
            "overlapping_years": list(overlapping_accounting_years(company, start_date, end_date)),
            "booked_count": booked_qs.count(),
            "not_booked_count": not_booked_qs.count(),
            "bank": bank_transaction_counts(company, start_date, end_date),
            "reports": reports_counts(company, start_date, end_date),
        }

    jobs = ExportJob.objects.filter(company=company)

    return render(request, "bookkeeping/export_bundle.html", {"form": form, "preview": preview, "jobs": jobs})


@login_required
@require_POST
@require_compliance_action("export.bundle")
@company_required
def export_bundle_start(request, company):
    form = ExportBundleForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Ogiltig period. Kontrollera start- och slutdatum.")
        return redirect("bookkeeping:export_bundle")

    start_export_job(
        company,
        form.cleaned_data["start_date"],
        form.cleaned_data["end_date"],
        requested_by=request.user,
    )
    messages.success(request, "Exporten har startats och körs i bakgrunden.")
    return redirect("bookkeeping:export_bundle")


@login_required
@require_compliance_action("export.bundle")
@company_required
def export_bundle_download(request, company, job_id):
    job = get_object_or_404(ExportJob, pk=job_id, company=company)
    if job.status != ExportJob.Status.COMPLETED or not job.file:
        messages.error(request, "Exportpaketet är inte klart för nedladdning.")
        return redirect("bookkeeping:export_bundle")

    return FileResponse(
        job.file.open("rb"),
        as_attachment=True,
        filename=job.file.name.rsplit("/", 1)[-1],
        content_type="application/zip",
    )


@login_required
@require_POST
@require_compliance_action("export.bundle")
@company_required
def export_bundle_delete(request, company, job_id):
    job = get_object_or_404(ExportJob, pk=job_id, company=company)
    if job.file:
        job.file.delete(save=False)
    job.delete()
    messages.success(request, "Exportpaketet har tagits bort.")
    return redirect("bookkeeping:export_bundle")
