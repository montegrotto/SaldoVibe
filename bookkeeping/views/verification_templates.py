"""Verifikationsmallar and the voucher series (verifikationsserie) rules."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..compliance_policy import require_compliance_action
from ..forms import (
    VerificationTemplateEntryFormSet,
    VerificationTemplateForm,
    VoucherSeriesRuleFormSet,
)
from ..models import (
    Account,
    VerificationTemplate,
    VoucherSeries,
    VoucherSeriesRule,
)
from ..verification_template_catalog import catalog_by_category, import_catalog_templates_for_company
from ._base import company_required

logger = logging.getLogger(__name__)


@login_required
@company_required
def verification_template_list(request, company):

    templates = (
        VerificationTemplate.objects.filter(company=company).annotate(entry_count=Count("entries")).order_by("name")
    )
    return render(
        request,
        "bookkeeping/verification_template_list.html",
        {
            "templates": templates,
        },
    )


@login_required
@company_required
def verification_template_library(request, company):

    if request.method == "POST":
        slugs = request.POST.getlist("slugs")
        if not slugs:
            messages.error(request, "Välj minst en mall att importera.")
            return redirect("bookkeeping:verification_template_library")

        created, updated, skipped = import_catalog_templates_for_company(company, slugs)

        if created or updated:
            parts = []
            if created:
                parts.append(f"{created} ny{'a' if created != 1 else ''}")
            if updated:
                parts.append(f"{updated} uppdaterad{'e' if updated != 1 else ''}")
            messages.success(request, f"Importerade mallar: {', '.join(parts)}.")
        for slug, reason in skipped:
            messages.warning(request, f"Hoppade över '{slug}': {reason}")

        return redirect("bookkeeping:verification_template_list")

    imported_slugs = set(
        VerificationTemplate.objects.filter(company=company).exclude(slug="").values_list("slug", flat=True)
    )
    company_account_numbers = set(Account.objects.filter(company=company).values_list("number", flat=True))

    categories = []
    for category, templates in catalog_by_category():
        rows = []
        for template in templates:
            numbers = [entry["account"] for entry in template["entries"]]
            missing = sorted({number for number in numbers if number not in company_account_numbers})
            rows.append(
                {
                    "slug": template["slug"],
                    "name": template["name"],
                    "description": template["description"],
                    "source_url": template["source_url"],
                    "accounts": numbers,
                    "missing_accounts": missing,
                    "is_imported": template["slug"] in imported_slugs,
                }
            )
        categories.append((category, rows))

    return render(
        request,
        "bookkeeping/verification_template_library.html",
        {
            "categories": categories,
            "imported_count": len(imported_slugs),
        },
    )


@login_required
@company_required
def verification_template_create(request, company):

    if request.method == "POST":
        form = VerificationTemplateForm(request.POST)
        formset = VerificationTemplateEntryFormSet(request.POST, form_kwargs={"company": company})
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                template = form.save(commit=False)
                template.company = company
                template.save()

                formset.instance = template
                entries = formset.save(commit=False)
                for idx, entry in enumerate(entries):
                    entry.template = template
                    entry.sort_order = idx
                    entry.save()
                for deleted_entry in formset.deleted_objects:
                    deleted_entry.delete()

            messages.success(request, "Verifikationsmallen har skapats.")
            return redirect("bookkeeping:verification_template_list")

        messages.error(request, "Kunde inte skapa verifikationsmallen. Kontrollera formuläret och försök igen.")
    else:
        form = VerificationTemplateForm()
        formset = VerificationTemplateEntryFormSet(form_kwargs={"company": company})

    return render(
        request,
        "bookkeeping/verification_template_form.html",
        {
            "form": form,
            "formset": formset,
            "page_title_text": "Ny verifikationsmall",
            "submit_label": "Skapa mall",
        },
    )


@login_required
@company_required
def verification_template_update(request, company, pk):

    template = get_object_or_404(VerificationTemplate, pk=pk, company=company)

    if request.method == "POST":
        form = VerificationTemplateForm(request.POST, instance=template)
        formset = VerificationTemplateEntryFormSet(
            request.POST,
            instance=template,
            form_kwargs={"company": company},
        )
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                template = form.save()
                entries = formset.save(commit=False)
                for idx, entry in enumerate(entries):
                    entry.template = template
                    entry.sort_order = idx
                    entry.save()
                for deleted_entry in formset.deleted_objects:
                    deleted_entry.delete()

            messages.success(request, "Verifikationsmallen har uppdaterats.")
            return redirect("bookkeeping:verification_template_list")

        messages.error(request, "Kunde inte uppdatera verifikationsmallen. Kontrollera formuläret och försök igen.")
    else:
        form = VerificationTemplateForm(instance=template)
        formset = VerificationTemplateEntryFormSet(instance=template, form_kwargs={"company": company})

    return render(
        request,
        "bookkeeping/verification_template_form.html",
        {
            "form": form,
            "formset": formset,
            "template_obj": template,
            "page_title_text": "Redigera verifikationsmall",
            "submit_label": "Spara",
        },
    )


@login_required
@require_POST
@company_required
def verification_template_delete(request, company, pk):

    template = get_object_or_404(VerificationTemplate, pk=pk, company=company)
    template_name = template.name
    template.delete()
    messages.success(request, f"Verifikationsmallen '{template_name}' har tagits bort.")
    return redirect("bookkeeping:verification_template_list")


@login_required
@require_compliance_action("voucher_series.manage")
@company_required
def voucher_series_settings(request, company):

    VoucherSeriesRule.seed_defaults_for_company(company)
    queryset = VoucherSeriesRule.objects.filter(company=company).order_by("source")

    if request.method == "POST":
        formset = VoucherSeriesRuleFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            formset.save()
            messages.success(request, "Verifikationsserierna har uppdaterats.")
            return redirect("bookkeeping:voucher_series_settings")
    else:
        formset = VoucherSeriesRuleFormSet(queryset=queryset)

    active_series = (
        VoucherSeries.objects.filter(company=company)
        .select_related("accounting_year")
        .order_by("-accounting_year__start_date", "code")
    )

    return render(
        request,
        "bookkeeping/voucher_series_settings.html",
        {
            "formset": formset,
            "active_series": active_series,
        },
    )
