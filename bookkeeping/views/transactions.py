"""Verifikationer: listing, entry, detail, attachments and reversal."""

import logging
from decimal import Decimal
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from attachments.services import first_extraction_suggestion
from attachments.view_helpers import (
    add_attachments,
    attachment_panel_context,
    picker_selection,
    remove_attachment,
)

from ..balances import build_account_balances
from ..compliance_policy import require_compliance_action
from ..forms import (
    JournalEntryFormSet,
    PeriodizationForm,
    TransactionForm,
)
from ..models import (
    Account,
    AccountingYear,
    JournalEntry,
    Transaction,
    VerificationTemplate,
)
from ..period_locking import is_date_locked
from ..periodization import create_periodization_vouchers
from ..reports import (
    default_accounting_year,
    get_year_context,
)
from ..sie import encode_sie, generate_sie4_content
from ._base import company_required

logger = logging.getLogger(__name__)


def _get_active_verification_templates(company):
    return (
        VerificationTemplate.objects.filter(company=company, is_active=True)
        .prefetch_related("entries__account")
        .order_by("name")
    )


@login_required
@company_required
def transaction_list(request, company):

    years, selected_year = get_year_context(request, company)
    selected_account = None
    account_id = request.GET.get("account")
    if account_id:
        selected_account = Account.objects.filter(company=company, pk=account_id).first()

    transactions = Transaction.objects.filter(accounting_year__company=company).prefetch_related(
        "entries__account", "accounting_year"
    )
    if selected_year:
        transactions = transactions.filter(accounting_year=selected_year)
    if selected_account is not None:
        transactions = transactions.filter(entries__account=selected_account).distinct()
    transactions = transactions.order_by("-date", "-created_at")
    page_obj = Paginator(transactions, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "bookkeeping/transaction_list.html",
        {
            "transactions": page_obj,
            "page_obj": page_obj,
            "years": years,
            "selected_year": selected_year,
            "selected_account": selected_account,
            "current_full_path": request.get_full_path(),
            "transaction_list_year_url": reverse("bookkeeping:transaction_list")
            + (f"?year={selected_year.pk}" if selected_year else ""),
        },
    )


@login_required
@require_compliance_action("export.sie4")
@company_required
def sie_export(request, company):

    years = AccountingYear.objects.filter(company=company).order_by("-start_date", "-id")
    selected_year = None
    year_id = request.GET.get("year")
    if year_id:
        selected_year = years.filter(pk=year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(years)

    if selected_year is None:
        messages.error(request, "Inget räkenskapsår finns att exportera.")
        return redirect("bookkeeping:accounting_year_list")

    transactions = Transaction.objects.filter(accounting_year=selected_year).prefetch_related("entries__account")
    sie_content = generate_sie4_content(company, selected_year, transactions)
    payload = encode_sie(sie_content)

    org_nr = (company.org_number or "").replace("-", "").replace(" ", "") or f"company-{company.pk}"
    filename = f"SIE4_{org_nr}_{selected_year.end_date.year}.se"
    response = HttpResponse(payload, content_type="text/plain; charset=cp437")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@company_required
def transaction_add(request, company):
    from banking.services import get_managed_bank_accounts

    verification_templates = _get_active_verification_templates(company)
    bank_managed_accounts = {
        str(account_id): {"bank_account_id": bank_account.pk}
        for account_id, bank_account in get_managed_bank_accounts(company).items()
    }

    if not AccountingYear.objects.filter(company=company).exists():
        messages.error(request, "Skapa minst ett räkenskapsår innan du registrerar verifikationer.")
        return redirect("bookkeeping:accounting_year_list")

    selected_attachment_ids, selected_attachments = picker_selection(request, company)
    extraction_suggestion = None

    if request.method == "POST":
        saved = False
        selected_template = None
        form = TransactionForm(request.POST, company=company)
        formset = JournalEntryFormSet(request.POST, form_kwargs={"company": company})
        if form.is_valid() and formset.is_valid():
            txn_date = form.cleaned_data["date"]
            if is_date_locked(company, txn_date):
                messages.error(
                    request,
                    "Den valda perioden är låst. Skapa en korrigeringsverifikation i öppen period eller lås upp perioden.",
                )
            else:
                matching_years = AccountingYear.objects.filter(
                    company=company,
                    start_date__lte=txn_date,
                    end_date__gte=txn_date,
                ).order_by("-start_date", "-id")

                if not matching_years.exists():
                    messages.error(request, "Inget räkenskapsår matchar valt datum.")
                elif matching_years.count() > 1:
                    messages.error(request, "Flera räkenskapsår matchar valt datum. Kontrollera räkenskapsåren.")
                else:
                    entries = [f for f in formset if f.cleaned_data and not f.cleaned_data.get("DELETE")]
                    total_debit = sum(f.cleaned_data.get("debit", Decimal("0")) for f in entries)
                    total_credit = sum(f.cleaned_data.get("credit", Decimal("0")) for f in entries)
                    if total_debit != total_credit:
                        messages.error(
                            request, f"Verifikationen är inte i balans. Debet: {total_debit} – Kredit: {total_credit}"
                        )
                    else:
                        with db_transaction.atomic():
                            txn = form.save(commit=False)
                            txn.accounting_year = matching_years.first()
                            txn.created_by = request.user
                            txn.save()
                            formset.instance = txn
                            formset.save()
                            txn.validate_balanced()
                            if selected_attachments.exists():
                                txn.attachments.add(*selected_attachments)
                        messages.success(request, "Verifikationen har sparats.")
                        saved = True
        else:
            error_messages = []

            for field_name, field_errors in form.errors.items():
                if field_name == "__all__":
                    error_messages.extend(field_errors)
                    continue
                label = form.fields[field_name].label if field_name in form.fields else field_name
                error_messages.extend([f"{label}: {err}" for err in field_errors])

            error_messages.extend([str(err) for err in formset.non_form_errors()])

            for idx, entry_form in enumerate(formset.forms, start=1):
                for field_name, field_errors in entry_form.errors.items():
                    if field_name == "__all__":
                        error_messages.extend([f"Rad {idx}: {err}" for err in field_errors])
                        continue
                    label = entry_form.fields[field_name].label if field_name in entry_form.fields else field_name
                    error_messages.extend([f"Rad {idx}, {label}: {err}" for err in field_errors])

            if error_messages:
                messages.error(request, "Kunde inte spara verifikationen. " + " ".join(error_messages[:3]))
            else:
                messages.error(request, "Kunde inte spara verifikationen. Kontrollera formuläret och försök igen.")

        if saved:
            return redirect("bookkeeping:transaction_list")
    else:
        selected_template = None
        entries_initial = None
        selected_template_id = request.GET.get("template")
        if selected_template_id:
            selected_template = verification_templates.filter(pk=selected_template_id).first()
            if selected_template is None:
                messages.error(request, "Den valda verifikationsmallen kunde inte hittas.")
            else:
                entries_initial = [
                    {
                        "account": entry.account_id,
                    }
                    for entry in selected_template.entries.all()
                ]

        extraction_suggestion = first_extraction_suggestion(selected_attachments)
        transaction_initial = {}
        if extraction_suggestion:
            if extraction_suggestion.get("datum"):
                transaction_initial["date"] = extraction_suggestion["datum"]
            # Mallens egen beskrivning (nedan) väger tyngre än ett OCR-gissat
            # leverantörsnamn när en mall är vald - det är ett medvetet val.
            if extraction_suggestion.get("leverantör") and selected_template is None:
                transaction_initial["description"] = extraction_suggestion["leverantör"]

        form = TransactionForm(company=company, initial=transaction_initial)
        if selected_template is not None:
            form.fields["description"].initial = selected_template.description or selected_template.name
        formset = JournalEntryFormSet(initial=entries_initial, form_kwargs={"company": company})

    account_balances = build_account_balances(company)

    return render(
        request,
        "bookkeeping/transaction_form.html",
        {
            "form": form,
            "formset": formset,
            "account_balances": account_balances,
            "bank_managed_accounts": bank_managed_accounts,
            "verification_templates": verification_templates,
            "selected_template": selected_template,
            "verification_templates_payload": {
                str(template.pk): {
                    "description": template.description,
                    "base_amount_label": template.base_amount_label,
                    "entries": [
                        {
                            "account_id": entry.account_id,
                            "side": "debit" if entry.is_debit else "credit",
                            "amount_rule": entry.amount_rule,
                            "amount_percent": (str(entry.amount_percent) if entry.amount_percent is not None else None),
                        }
                        for entry in template.entries.all()
                    ],
                }
                for template in verification_templates
            },
            "selected_attachments": selected_attachments,
            "selected_attachment_ids_csv": ",".join(str(attachment_id) for attachment_id in selected_attachment_ids),
            "picker_return_to": request.get_full_path(),
            "extraction_applied": bool(extraction_suggestion),
            "extraction_suggested_base_amount": (extraction_suggestion or {}).get("totalbelopp") or "",
        },
    )


@login_required
@company_required
def periodization_create(request, company):

    if not AccountingYear.objects.filter(company=company).exists():
        messages.error(request, "Skapa minst ett räkenskapsår innan du registrerar periodiseringar.")
        return redirect("bookkeeping:accounting_year_list")

    if request.method == "POST":
        form = PeriodizationForm(request.POST, company=company)
        if form.is_valid():
            try:
                created = create_periodization_vouchers(
                    company,
                    request.user,
                    description=form.cleaned_data["description"],
                    amount=form.cleaned_data["amount"],
                    account=form.cleaned_data["account"],
                    counter_account=form.cleaned_data["counter_account"],
                    months=form.cleaned_data["months"],
                    start_date=form.cleaned_data["start_date"],
                )
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                messages.success(
                    request,
                    f"{len(created)} verifikationer skapade för periodiseringen "
                    f"({created[0].date:%Y-%m} – {created[-1].date:%Y-%m}).",
                )
                return redirect("bookkeeping:transaction_list")
    else:
        form = PeriodizationForm(company=company)

    return render(
        request,
        "bookkeeping/periodization_form.html",
        {"form": form},
    )


def _linked_source_entries(txn):
    entries = []
    return_to = f"?return_to={quote(reverse('bookkeeping:transaction_detail', args=[txn.pk]))}"

    for bank_tx in txn.bank_transaction_sources.all():
        entries.append(
            {
                "icon": "bi-bank",
                "label": f"Banktransaktion: {bank_tx.bank_account.name} · {bank_tx.date} · {bank_tx.amount:.2f} kr",
                "url": reverse("banking:transaction_list") + f"?bank_account={bank_tx.bank_account_id}",
            }
        )

    for claim in txn.expense_claims.all():
        entries.append(
            {
                "icon": "bi-wallet2",
                "label": f"Utlägg: {claim.description} ({claim.person_display_name})",
                "url": reverse("expenses:expense_detail", args=[claim.pk]) + return_to,
            }
        )
    for claim in txn.expense_claim_payment_transactions.all():
        entries.append(
            {
                "icon": "bi-wallet2",
                "label": f"Utläggsbetalning: {claim.description} ({claim.person_display_name})",
                "url": reverse("expenses:expense_detail", args=[claim.pk]) + return_to,
            }
        )

    supplier_invoice = getattr(txn, "supplier_invoice", None)
    if supplier_invoice is not None:
        entries.append(
            {
                "icon": "bi-receipt",
                "label": (
                    f"Leverantörsfaktura: {supplier_invoice.invoice_number or 'Utan fakturanummer'}"
                    f" - {supplier_invoice.supplier_display_name}"
                ),
                "url": reverse("supplier_invoices:invoice_detail", args=[supplier_invoice.pk]) + return_to,
            }
        )
    for invoice in txn.supplier_invoice_payment_transactions.all():
        entries.append(
            {
                "icon": "bi-receipt",
                "label": (
                    f"Leverantörsfakturabetalning: {invoice.invoice_number or 'Utan fakturanummer'}"
                    f" - {invoice.supplier_display_name}"
                ),
                "url": reverse("supplier_invoices:invoice_detail", args=[invoice.pk]) + return_to,
            }
        )

    outgoing_invoice = getattr(txn, "outgoing_invoice", None)
    if outgoing_invoice is not None:
        entries.append(
            {
                "icon": "bi-file-earmark-text",
                "label": (
                    f"Kundfaktura: {outgoing_invoice.invoice_number or f'Faktura {outgoing_invoice.pk}'}"
                    f" - {outgoing_invoice.customer.name}"
                ),
                "url": reverse("invoicing:invoice_detail", args=[outgoing_invoice.pk]) + return_to,
            }
        )
    for invoice in txn.outgoing_invoice_payments.all():
        entries.append(
            {
                "icon": "bi-file-earmark-text",
                "label": (
                    f"Kundfakturabetalning: {invoice.invoice_number or f'Faktura {invoice.pk}'}"
                    f" - {invoice.customer.name}"
                ),
                "url": reverse("invoicing:invoice_detail", args=[invoice.pk]) + return_to,
            }
        )

    payroll_run = getattr(txn, "payroll_run_booking", None)
    if payroll_run is not None:
        entries.append(
            {
                "icon": "bi-people",
                "label": f"Lönekörning {payroll_run.period_year}-{payroll_run.period_month:02d}",
                "url": reverse("payroll:payroll_run_detail", args=[payroll_run.pk]),
            }
        )

    depreciation = getattr(txn, "fixed_asset_depreciation", None)
    if depreciation is not None:
        entries.append(
            {
                "icon": "bi-building-gear",
                "label": f"Avskrivning: {depreciation.fixed_asset.name}",
                "url": reverse("fixed_assets:asset_detail", args=[depreciation.fixed_asset_id]) + return_to,
            }
        )

    impairment = getattr(txn, "fixed_asset_impairment", None)
    if impairment is not None:
        entries.append(
            {
                "icon": "bi-building-gear",
                "label": f"Nedskrivning: {impairment.fixed_asset.name}",
                "url": reverse("fixed_assets:asset_detail", args=[impairment.fixed_asset_id]) + return_to,
            }
        )

    return entries


@login_required
@company_required
def transaction_detail(request, company, pk):

    txn = get_object_or_404(
        Transaction.objects.filter(accounting_year__company=company).prefetch_related(
            "entries__account", "attachments", "bank_transaction_sources__bank_account"
        ),
        pk=pk,
    )

    back_url = reverse("bookkeeping:transaction_list")
    requested_back_url = request.GET.get("return_to", "")
    if requested_back_url and url_has_allowed_host_and_scheme(
        url=requested_back_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        back_url = requested_back_url

    is_payment_transaction = (
        txn.supplier_invoice_payment_transactions.exists()
        or txn.outgoing_invoice_payments.exists()
        or txn.expense_claim_payment_transactions.exists()
    )

    return render(
        request,
        "bookkeeping/transaction_detail.html",
        {
            "txn": txn,
            "back_url": back_url,
            "linked_sources": _linked_source_entries(txn),
            **attachment_panel_context(
                request,
                company=company,
                document=txn,
                period_date=txn.date,
                attach_url=reverse("bookkeeping:transaction_attachment_add", args=[txn.pk]),
                detach_url=reverse("bookkeeping:transaction_attachment_remove", args=[txn.pk]),
            ),
            "is_payment_transaction": is_payment_transaction,
        },
    )


@login_required
@require_POST
@company_required
def transaction_attachment_add(request, company, pk):
    txn = get_object_or_404(Transaction.objects.filter(accounting_year__company=company), pk=pk)
    return add_attachments(
        request,
        company=company,
        document=txn,
        period_date=txn.date,
        redirect_url=reverse("bookkeeping:transaction_detail", args=[txn.pk]),
    )


@login_required
@require_POST
@company_required
def transaction_attachment_remove(request, company, pk):
    txn = get_object_or_404(Transaction.objects.filter(accounting_year__company=company), pk=pk)
    return remove_attachment(
        request,
        company=company,
        document=txn,
        period_date=txn.date,
        redirect_url=reverse("bookkeeping:transaction_detail", args=[txn.pk]),
        document_noun="verifikationen",
    )


@login_required
@require_POST
@company_required
def transaction_reverse(request, company, pk):

    txn = get_object_or_404(
        Transaction.objects.filter(accounting_year__company=company).prefetch_related(
            "entries__account", "corrections"
        ),
        pk=pk,
    )

    if txn.corrections.exists():
        messages.info(request, "Verifikationen har redan en registrerad korrigering.")
        return redirect("bookkeeping:transaction_detail", pk=txn.pk)

    if (
        txn.supplier_invoice_payment_transactions.exists()
        or txn.outgoing_invoice_payments.exists()
        or txn.expense_claim_payment_transactions.exists()
    ):
        messages.error(
            request,
            'Detta är en betalningsverifikation. Använd "Ångra betalning" på fakturan/utlägget för att '
            "ångra den och släppa ev. banktransaktion fri för ny bokföring.",
        )
        return redirect("bookkeeping:transaction_detail", pk=txn.pk)

    reversal_date = timezone.localdate()
    if is_date_locked(company, reversal_date):
        messages.error(request, "Dagens period är låst. Lås upp perioden eller välj en öppen period för korrigering.")
        return redirect("bookkeeping:transaction_detail", pk=txn.pk)

    matching_years = AccountingYear.objects.filter(
        company=company,
        start_date__lte=reversal_date,
        end_date__gte=reversal_date,
    ).order_by("-start_date", "-id")

    if not matching_years.exists():
        messages.error(request, "Ingen öppen period/räkenskapsår hittades för korrigeringsdatumet.")
        return redirect("bookkeeping:transaction_detail", pk=txn.pk)
    if matching_years.count() > 1:
        messages.error(request, "Flera räkenskapsår matchar korrigeringsdatumet. Kontrollera räkenskapsåren.")
        return redirect("bookkeeping:transaction_detail", pk=txn.pk)

    with db_transaction.atomic():
        reversal = Transaction.objects.create(
            accounting_year=matching_years.first(),
            date=reversal_date,
            description=f"Korrigering av verifikation {txn.reference or txn.pk}",
            created_by=request.user,
            correction_of=txn,
        )

        for entry in txn.entries.all():
            JournalEntry.objects.create(
                transaction=reversal,
                account=entry.account,
                debit=entry.credit,
                credit=entry.debit,
                description=f"Korrigering av {txn.reference or txn.pk}"[:300],
            )

        reversal.validate_balanced()

    messages.success(
        request,
        f"Korrigeringsverifikation skapad: {reversal.voucher_series}{reversal.voucher_number}.",
    )
    return redirect("bookkeeping:transaction_detail", pk=reversal.pk)
