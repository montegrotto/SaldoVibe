"""Company CRUD and switching the session's active company."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from attachments.email_import import import_email_attachments_for_company
from invoicing.models import Invoice
from payroll.models import PayrollRun
from supplier_invoices.models import SupplierInvoice

from ..bas_accounts import BasAccountLoadError, seed_bas_2026_accounts_for_company
from ..company_scope import (
    SESSION_COMPANY_KEY,
    can_create_company,
    get_user_companies,
    set_active_company,
)
from ..compliance_policy import require_compliance_action
from ..forms import (
    CompanyForm,
)
from ..models import (
    AccountingYear,
    Company,
    JournalEntry,
    Transaction,
    VerificationTemplate,
    VoucherSeriesRule,
)

logger = logging.getLogger(__name__)


@login_required
def company_list(request):
    companies = get_user_companies(request.user)

    return render(
        request,
        "bookkeeping/company_list.html",
        {
            "companies": companies,
        },
    )


@login_required
def no_company_access(request):
    if can_create_company(request.user):
        return redirect("bookkeeping:company_create")
    companies = get_user_companies(request.user)
    if companies.count() > 1:
        return redirect("bookkeeping:select_company")
    if companies.exists():
        return redirect("bookkeeping:dashboard")
    return render(request, "bookkeeping/no_company_access.html")


@login_required
def select_company(request):
    companies = get_user_companies(request.user)
    if not companies.exists():
        if can_create_company(request.user):
            return redirect("bookkeeping:company_create")
        return redirect("bookkeeping:no_company_access")

    if request.method == "POST":
        company_id = request.POST.get("company_id")
        company = companies.filter(pk=company_id).first()
        if company is None:
            messages.error(request, "Ogiltigt val av företag.")
        else:
            set_active_company(request, company)
            logger.info("Active company selected", extra={"company_id": company.id, "user_id": request.user.id})
            return redirect("bookkeeping:dashboard")

    return render(request, "bookkeeping/select_company.html", {"companies": companies})


@login_required
def company_create(request):
    if not can_create_company(request.user):
        logger.warning("Company create denied: missing permission", extra={"user_id": request.user.id})
        messages.error(request, "Du har inte behörighet att skapa företag. Kontakta administratör.")
        if get_user_companies(request.user).exists():
            return redirect("bookkeeping:company_list")
        return redirect("bookkeeping:no_company_access")

    if request.method == "POST":
        form = CompanyForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with db_transaction.atomic():
                    company = form.save(commit=False)
                    if not request.user.is_staff:
                        # Non-staff users should not be able to create inactive companies.
                        company.is_active = True
                    company.save()
                    company.users.add(request.user)
                    created_accounts = seed_bas_2026_accounts_for_company(company)
                    VoucherSeriesRule.seed_defaults_for_company(company)
            except BasAccountLoadError as exc:
                logger.exception(
                    "Company create failed while loading BAS accounts",
                    extra={"user_id": request.user.id},
                )
                messages.error(request, f"Kunde inte ladda BAS 2026-kontoplanen: {exc}")
                return render(
                    request,
                    "bookkeeping/company_form.html",
                    {
                        "form": form,
                        "page_title_text": "Nytt företag",
                        "submit_label": "Skapa företag",
                    },
                )

            set_active_company(request, company)
            logger.info(
                "Company created",
                extra={
                    "company_id": company.id,
                    "user_id": request.user.id,
                    "company_name": company.name,
                    "created_accounts": created_accounts,
                },
            )
            if company.email_fetch_enabled:
                # Deliberately not fetching here: creating a company must not block on
                # an unreachable mailbox. The scheduled job picks it up, and the company
                # edit page has a "hämta nu" button for immediate feedback.
                messages.info(
                    request,
                    "E-posthämtning är påslagen. Bilagor hämtas automatiskt av det schemalagda "
                    "jobbet, eller direkt via 'Hämta e-postbilagor' på företagssidan.",
                )
            messages.success(
                request,
                f"Företaget har skapats, BAS 2026-kontoplanen ({created_accounts} konton) har lagts in och företaget har valts som aktivt.",
            )
            return redirect("bookkeeping:dashboard")

        logger.warning(
            "Company create failed validation",
            extra={"user_id": request.user.id, "errors": str(form.errors)},
        )
        messages.error(request, "Kunde inte skapa företag. Kontrollera formuläret och försök igen.")
    else:
        form = CompanyForm()

    return render(
        request,
        "bookkeeping/company_form.html",
        {
            "form": form,
            "page_title_text": "Nytt företag",
            "submit_label": "Skapa företag",
        },
    )


@login_required
def company_update(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if not request.user.is_superuser and not company.users.filter(pk=request.user.pk).exists():
        logger.warning(
            "Unauthorized company update attempt",
            extra={"company_id": company.id, "user_id": request.user.id},
        )
        messages.error(request, "Du har inte behörighet att redigera detta företag.")
        return redirect("bookkeeping:company_list")

    if request.method == "POST":
        existing_password = company.email_fetch_password
        existing_oauth_client_secret = company.email_fetch_oauth_client_secret
        existing_smtp_password = company.email_send_smtp_password
        existing_notify_smtp_password = company.email_notify_smtp_password
        form = CompanyForm(request.POST, request.FILES, instance=company)
        if form.is_valid():
            updated = form.save(commit=False)
            if not request.POST.get("email_fetch_password"):
                updated.email_fetch_password = existing_password
            if not request.POST.get("email_fetch_oauth_client_secret"):
                updated.email_fetch_oauth_client_secret = existing_oauth_client_secret
            if not request.POST.get("email_send_smtp_password"):
                updated.email_send_smtp_password = existing_smtp_password
            if not request.POST.get("email_notify_smtp_password"):
                updated.email_notify_smtp_password = existing_notify_smtp_password
            if not request.user.is_staff:
                # Non-staff users should not be able to deactivate companies.
                updated.is_active = True
            updated.save()
            logger.info(
                "Company updated",
                extra={"company_id": updated.id, "user_id": request.user.id, "company_name": updated.name},
            )
            messages.success(request, f"Företaget {updated.name} har uppdaterats.")

            should_fetch = request.POST.get("fetch_email_attachments") == "1"
            if should_fetch:
                logger.info(
                    "Email fetch requested on company update",
                    extra={"company_id": updated.id, "provider": updated.email_fetch_provider},
                )
                try:
                    result = import_email_attachments_for_company(company=updated, user=request.user)
                    logger.info("Email fetch completed on company update", extra={"company_id": updated.id, **result})
                    messages.success(
                        request,
                        (
                            "E-postbilagor hämtades. "
                            f"Importerade: {result['imported']}, "
                            f"Dubbletter: {result['duplicates']}, "
                            f"Ej stödda format: {result['skipped_unsupported']}."
                        ),
                    )
                except Exception as exc:
                    logger.exception("Email fetch failed on company update", extra={"company_id": updated.id})
                    messages.error(request, f"Kunde inte hämta e-postbilagor: {exc}")
            return redirect("bookkeeping:company_list")

        logger.warning(
            "Company update failed validation",
            extra={"company_id": company.id, "user_id": request.user.id, "errors": str(form.errors)},
        )
        messages.error(request, "Kunde inte uppdatera företag. Kontrollera formuläret och försök igen.")
    else:
        form = CompanyForm(instance=company)

    return render(
        request,
        "bookkeeping/company_form.html",
        {
            "form": form,
            "company": company,
            "page_title_text": "Redigera företag",
            "submit_label": "Spara",
        },
    )


@login_required
@require_POST
@require_compliance_action("company.delete")
def company_delete(request, pk):
    company = get_object_or_404(Company, pk=pk)
    if not request.user.is_superuser and not company.users.filter(pk=request.user.pk).exists():
        logger.warning(
            "Unauthorized company delete attempt",
            extra={"company_id": company.id, "user_id": request.user.id},
        )
        messages.error(request, "Du har inte behörighet att ta bort detta företag.")
        return redirect("bookkeeping:company_list")

    company_name = company.name
    if (
        AccountingYear.objects.filter(company=company).exists()
        or Transaction.objects.filter(accounting_year__company=company).exists()
        or JournalEntry.objects.filter(account__company=company).exists()
        or SupplierInvoice.objects.filter(company=company).exists()
        or Invoice.objects.filter(company=company).exists()
        or PayrollRun.objects.filter(company=company).exists()
    ):
        messages.error(
            request,
            "Företaget kan inte tas bort eftersom bokföringsdata finns. Avaktivera företaget istället för att bevara revisionsspår.",
        )
        return redirect("bookkeeping:company_update", pk=pk)

    try:
        with db_transaction.atomic():
            # Configuration objects protect the company's accounts (and
            # customers) via on_delete=PROTECT, so they must go first. The
            # guard above guarantees no accounting data references them.
            from fixed_assets.models import FixedAssetType
            from invoicing.models import Article, RecurringInvoice

            company.bank_accounts.all().delete()
            FixedAssetType.objects.filter(company=company).delete()
            VerificationTemplate.objects.filter(company=company).delete()
            RecurringInvoice.objects.filter(company=company).delete()
            Article.objects.filter(company=company).delete()
            company.delete()
    except ProtectedError as exc:
        logger.exception(
            "Company delete blocked by protected relations",
            extra={"company_id": company.id, "user_id": request.user.id, "protected_objects": str(exc)},
        )
        messages.error(
            request,
            "Företaget kunde inte tas bort eftersom relaterad data fortfarande är låst av skyddade kopplingar.",
        )
        return redirect("bookkeeping:company_update", pk=pk)

    if request.session.get(SESSION_COMPANY_KEY) == pk:
        request.session.pop(SESSION_COMPANY_KEY, None)
        replacement_company = get_user_companies(request.user).first()
        if replacement_company is not None:
            set_active_company(request, replacement_company)

    logger.info(
        "Company deleted",
        extra={"company_id": pk, "user_id": request.user.id, "company_name": company_name},
    )
    messages.success(request, f"Företaget {company_name} har tagits bort.")
    return redirect("bookkeeping:company_list")


@login_required
@require_POST
def switch_company(request):
    company_id = request.POST.get("company_id")
    if not company_id:
        logger.warning("Switch company failed: missing company_id", extra={"user_id": request.user.id})
        messages.error(request, "Inget företag valdes.")
        return redirect("bookkeeping:dashboard")

    company = get_object_or_404(Company, pk=company_id, is_active=True)
    if not request.user.is_superuser and not company.users.filter(pk=request.user.pk).exists():
        logger.warning(
            "Unauthorized company switch attempt",
            extra={"company_id": company.id, "user_id": request.user.id},
        )
        messages.error(request, "Du har inte behörighet till valt företag.")
        return redirect("bookkeeping:dashboard")

    set_active_company(request, company)
    logger.info("Active company switched", extra={"company_id": company.id, "user_id": request.user.id})
    messages.success(request, f"Aktivt företag: {company.name}")
    return redirect("bookkeeping:dashboard")
