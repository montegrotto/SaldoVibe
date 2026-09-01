"""SIE and SI file import, plus their diagnostics downloads."""

import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from auditlog.context import audit_source

from ..compliance_policy import require_compliance_action
from ..forms import (
    SIEImportForm,
    SIImportForm,
)
from ..models import (
    Account,
    AccountingYear,
    JournalEntry,
    PeriodLock,
    Transaction,
    TransactionSource,
)
from ..period_locking import is_date_locked, year_lock_status
from ..reports import default_accounting_year
from ..sie import (
    parse_sie_accounting_year,
    parse_sie_accounts,
    parse_sie_balances,
    parse_sie_verifications_with_diagnostics,
    reconcile_closing_balances,
)
from ..sie_import import (
    SI_REFERENCE_PREFIX,
    build_si_verification_preview,
    deserialize_sie_verifications,
    find_likely_duplicate_verifications,
    next_si_series_number,
    serialize_import_diagnostics_payload,
    serialize_sie_verifications,
    sie_diagnostics_session_key,
    sie_preview_session_key,
    summarize_import_diagnostics,
)
from ._base import company_required

logger = logging.getLogger(__name__)


def _account_for_import(company, account_number, accounts_from_file):
    account = Account.objects.filter(company=company, number=account_number).first()
    if account is None:
        account_class = account_number[0] if account_number and account_number[0] in "123456789" else "9"
        account = Account.objects.create(
            company=company,
            number=account_number,
            name=accounts_from_file.get(account_number) or f"Importerat konto {account_number}",
            account_class=account_class,
            is_active=True,
        )
    return account


OPENING_BALANCE_REFERENCE = "IB"


def _import_opening_balances(company, accounting_year, balances, accounts_from_file, user):
    """Bokför filens #IB 0-rader som en IB-verifikation på räkenskapsårets första dag.
    Returnerar en statustext till användaren ("" = inget gjordes)."""
    start = accounting_year.start_date
    if JournalEntry.objects.filter(transaction__accounting_year__company=company, transaction__date__lt=start).exists():
        return "Filens ingående balanser hoppades över: det finns redan bokföring före räkenskapsårets start."
    if is_date_locked(company, start):
        return "Filens ingående balanser hoppades över: räkenskapsårets första dag ligger i en låst period."
    if Transaction.objects.filter(
        accounting_year=accounting_year, date=start, reference=OPENING_BALANCE_REFERENCE
    ).exists():
        return "Filens ingående balanser hoppades över: en IB-verifikation finns redan."
    rows = {number: amount for number, amount in balances.items() if amount != 0}
    difference = sum(rows.values(), Decimal("0"))
    if difference != 0:
        return f"Filens ingående balanser hoppades över: raderna balanserar inte (differens {difference})."
    if not rows:
        return ""

    txn = Transaction.objects.create(
        accounting_year=accounting_year,
        date=start,
        description="Ingående balans",
        reference=OPENING_BALANCE_REFERENCE,
        created_by=user,
        source=TransactionSource.SIE_IMPORT,
    )
    for number, amount in rows.items():
        JournalEntry.objects.create(
            transaction=txn,
            account=_account_for_import(company, number, accounts_from_file),
            debit=amount if amount > 0 else Decimal("0"),
            credit=-amount if amount < 0 else Decimal("0"),
            description="",
        )
    txn.validate_balanced()
    return f"Ingående balanser: {len(rows)} konton bokförda som verifikation {OPENING_BALANCE_REFERENCE}."


def _locked_transactions_q(company):
    """Q matching Transaction.date values that fall inside an active PeriodLock for company."""
    locks = list(PeriodLock.objects.filter(company=company, is_locked=True).values_list("period_start", "period_end"))
    if not locks:
        return Q(pk__in=[])
    q = Q()
    for period_start, period_end in locks:
        q |= Q(date__gte=period_start, date__lte=period_end)
    return q


@login_required
@require_compliance_action("import.sie")
@company_required
def sie_import(request, company):

    logger.debug(
        "SIE import page opened", extra={"company_id": company.id, "user_id": request.user.id, "method": request.method}
    )

    if not AccountingYear.objects.filter(company=company).exists():
        messages.error(request, "Skapa minst ett räkenskapsår innan import av SIE.")
        return redirect("bookkeeping:accounting_year_list")

    selected_year = None
    requested_year_id = request.GET.get("year")
    if requested_year_id:
        selected_year = AccountingYear.objects.filter(company=company, pk=requested_year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(AccountingYear.objects.filter(company=company))

    selected_year_locked = selected_year is not None and year_lock_status(selected_year) == "locked"

    existing_transaction_count = 0
    deletable_transaction_count = 0
    kept_existing_count = 0
    if selected_year is not None:
        existing_qs = Transaction.objects.filter(accounting_year=selected_year)
        existing_transaction_count = existing_qs.count()
        if existing_transaction_count:
            # Replace may only remove verifications that SIE/SI import itself created
            # (redo of a botched migration); natively booked verifications must never
            # be deleted by an import (BFL/BFNAR 2013:2 — correction, not replacement).
            deletable_transaction_count = (
                existing_qs.filter(source=TransactionSource.SIE_IMPORT).exclude(_locked_transactions_q(company)).count()
            )
            kept_existing_count = existing_transaction_count - deletable_transaction_count
    diagnostics_key = sie_diagnostics_session_key(request.user.id, company.id, selected_year.id)

    if request.method == "POST":
        if selected_year_locked:
            messages.error(request, "Räkenskapsåret är låst. SIE-import är inte möjlig.")
            return redirect(f"{reverse('bookkeeping:sie_import')}?year={selected_year.pk}")

        form = SIEImportForm(request.POST, request.FILES, company=company)
        if form.is_valid():
            accounting_year = selected_year
            if accounting_year is None:
                messages.error(request, "Kunde inte avgöra räkenskapsår för importen.")
                return redirect("bookkeeping:accounting_year_list")

            if deletable_transaction_count > 0 and not form.cleaned_data.get("confirm_replace"):
                messages.error(
                    request,
                    "Du måste bekräfta att tidigare importerade verifikationer för räkenskapsåret tas bort innan importen kan starta.",
                )
                return render(
                    request,
                    "bookkeeping/sie_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                        "existing_transaction_count": existing_transaction_count,
                        "deletable_transaction_count": deletable_transaction_count,
                        "kept_existing_count": kept_existing_count,
                    },
                )

            upload = form.cleaned_data["sie_file"]
            payload = upload.read()

            text = None
            for encoding in ("utf-8-sig", "cp437", "latin-1"):
                try:
                    text = payload.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                logger.warning(
                    "SIE import failed: unsupported file encoding",
                    extra={"company_id": company.id, "user_id": request.user.id},
                )
                messages.error(request, "Kunde inte läsa SIE-filen. Kontrollera filkodning.")
                return render(
                    request,
                    "bookkeeping/sie_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                        "existing_transaction_count": existing_transaction_count,
                        "deletable_transaction_count": deletable_transaction_count,
                        "kept_existing_count": kept_existing_count,
                    },
                )

            file_year = parse_sie_accounting_year(text)
            if file_year is not None and file_year != (accounting_year.start_date, accounting_year.end_date):
                file_start, file_end = file_year
                messages.error(
                    request,
                    f"Filens räkenskapsår ({file_start:%Y-%m-%d} – {file_end:%Y-%m-%d}) stämmer inte med valt "
                    f"räkenskapsår ({accounting_year.start_date:%Y-%m-%d} – {accounting_year.end_date:%Y-%m-%d}). "
                    "Välj rätt räkenskapsår eller kontrollera filen.",
                )
                return render(
                    request,
                    "bookkeeping/sie_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                        "existing_transaction_count": existing_transaction_count,
                        "deletable_transaction_count": deletable_transaction_count,
                        "kept_existing_count": kept_existing_count,
                    },
                )

            verifications, diagnostics = parse_sie_verifications_with_diagnostics(text)
            accounts_from_file = parse_sie_accounts(text)
            expected_closing_balances = parse_sie_balances(text, "#UB")
            opening_balances = parse_sie_balances(text, "#IB")
            request.session[diagnostics_key] = serialize_import_diagnostics_payload("SIE", diagnostics)
            request.session.modified = True
            if not verifications and not opening_balances:
                summary = summarize_import_diagnostics(diagnostics)
                logger.warning(
                    "SIE import failed: no parseable verifications",
                    extra={"company_id": company.id, "user_id": request.user.id, "diagnostics": diagnostics[:10]},
                )
                messages.error(request, "Ogiltig SIE-fil: inga tolkbara verifikationer hittades.")
                if summary:
                    messages.warning(request, f"Detaljer: {summary}")
                return render(
                    request,
                    "bookkeeping/sie_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                        "existing_transaction_count": existing_transaction_count,
                        "deletable_transaction_count": deletable_transaction_count,
                        "kept_existing_count": kept_existing_count,
                    },
                )

            imported = 0
            skipped_existing = 0
            skipped_outside_year = 0
            skipped_unbalanced = 0
            skipped_locked_period = 0
            deleted_count = 0
            opening_status = ""

            with db_transaction.atomic():
                with audit_source("SIE-import"):
                    if deletable_transaction_count > 0:
                        to_delete = Transaction.objects.filter(
                            accounting_year=accounting_year,
                            source=TransactionSource.SIE_IMPORT,
                        ).exclude(_locked_transactions_q(company))
                        deleted_count = to_delete.count()
                        to_delete.delete()

                    if opening_balances:
                        opening_status = _import_opening_balances(
                            company, accounting_year, opening_balances, accounts_from_file, request.user
                        )

                    for ver in verifications:
                        ver_date = ver["date"]
                        if not (accounting_year.start_date <= ver_date <= accounting_year.end_date):
                            skipped_outside_year += 1
                            continue
                        if is_date_locked(company, ver_date):
                            skipped_locked_period += 1
                            continue

                        entries = ver.get("entries", [])
                        if len(entries) < 2:
                            skipped_unbalanced += 1
                            continue

                        total_debit = Decimal("0")
                        total_credit = Decimal("0")
                        for item in entries:
                            amount = item["amount"]
                            if amount >= 0:
                                total_debit += amount
                            else:
                                total_credit += -amount

                        if total_debit != total_credit:
                            skipped_unbalanced += 1
                            continue

                        reference = f"{ver['series']}{ver['number']}"
                        if Transaction.objects.filter(
                            accounting_year=accounting_year,
                            date=ver_date,
                            reference=reference,
                        ).exists():
                            skipped_existing += 1
                            continue

                        txn = Transaction.objects.create(
                            accounting_year=accounting_year,
                            date=ver_date,
                            description=ver["description"] or f"Importerad verifikation {reference}",
                            reference=reference,
                            created_by=request.user,
                            source=TransactionSource.SIE_IMPORT,
                        )

                        for item in entries:
                            account = _account_for_import(company, item["account"], accounts_from_file)

                            amount = item["amount"]
                            debit = amount if amount > 0 else Decimal("0")
                            credit = -amount if amount < 0 else Decimal("0")

                            JournalEntry.objects.create(
                                transaction=txn,
                                account=account,
                                debit=debit,
                                credit=credit,
                                description="",
                            )

                        txn.validate_balanced()
                        imported += 1

            messages.success(
                request,
                (
                    f"SIE-import klar. Borttagna befintliga verifikationer: {deleted_count}, "
                    f"Importerade: {imported}, "
                    f"Dubbletter: {skipped_existing}, "
                    f"Utanför räkenskapsår: {skipped_outside_year}, "
                    f"Låsta perioder: {skipped_locked_period}, "
                    f"Obalanserade: {skipped_unbalanced}."
                ),
            )
            if opening_status:
                messages.info(request, opening_status)
            if kept_existing_count:
                messages.info(
                    request,
                    (
                        f"{kept_existing_count} befintlig(a) verifikation(er) behölls: de ligger i låsta "
                        "perioder eller är bokförda direkt i SaldoVibe (inte via import) och kan därför "
                        "inte tas bort av en import."
                    ),
                )
            balance_mismatches = reconcile_closing_balances(company, accounting_year, expected_closing_balances)
            if balance_mismatches:
                request.session[diagnostics_key] = serialize_import_diagnostics_payload(
                    "SIE", diagnostics + balance_mismatches
                )
                request.session.modified = True
                messages.warning(
                    request,
                    (
                        f"Kontrollsumma: {len(balance_mismatches)} konton stämmer inte mot filens UB-saldon efter import. "
                        f"{summarize_import_diagnostics(balance_mismatches)}"
                    ),
                )
            if diagnostics:
                messages.warning(
                    request,
                    (
                        f"Importvalidering hittade {len(diagnostics)} radfel i filen. "
                        f"{summarize_import_diagnostics(diagnostics)}"
                    ),
                )
            logger.info(
                "SIE import completed",
                extra={
                    "company_id": company.id,
                    "user_id": request.user.id,
                    "deleted_count": deleted_count,
                    "imported": imported,
                    "skipped_existing": skipped_existing,
                    "skipped_outside_year": skipped_outside_year,
                    "skipped_locked_period": skipped_locked_period,
                    "skipped_unbalanced": skipped_unbalanced,
                },
            )
            return redirect(f"{reverse('bookkeeping:transaction_list')}?year={accounting_year.pk}")
    else:
        form = SIEImportForm(company=company)

    return render(
        request,
        "bookkeeping/sie_import.html",
        {
            "form": form,
            "selected_year": selected_year,
            "selected_year_locked": selected_year_locked,
            "existing_transaction_count": existing_transaction_count,
            "deletable_transaction_count": deletable_transaction_count,
            "kept_existing_count": kept_existing_count,
            "diagnostics_available": bool((request.session.get(diagnostics_key) or {}).get("count")),
        },
    )


@login_required
@require_compliance_action("import.sie")
@company_required
def sie_import_diagnostics_download(request, company):

    selected_year = None
    requested_year_id = request.GET.get("year")
    if requested_year_id:
        selected_year = AccountingYear.objects.filter(company=company, pk=requested_year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(AccountingYear.objects.filter(company=company))
    if selected_year is None:
        messages.error(request, "Inget räkenskapsår valt.")
        return redirect("bookkeeping:accounting_year_list")

    diagnostics_key = sie_diagnostics_session_key(request.user.id, company.id, selected_year.id)
    payload = request.session.get(diagnostics_key)
    if not payload:
        messages.error(request, "Ingen SIE-diagnostik finns sparad ännu. Ladda upp en fil först.")
        return redirect(f"{reverse('bookkeeping:sie_import')}?year={selected_year.pk}")

    content = json.dumps(payload, ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="sie-import-diagnostics-{selected_year.ending_year}.json"'
    return response


@login_required
@require_compliance_action("import.sie")
@company_required
def si_import(request, company):

    logger.debug(
        "SI import page opened", extra={"company_id": company.id, "user_id": request.user.id, "method": request.method}
    )

    if not AccountingYear.objects.filter(company=company).exists():
        messages.error(request, "Skapa minst ett räkenskapsår innan import av SI.")
        return redirect("bookkeeping:accounting_year_list")

    selected_year = None
    requested_year_id = request.GET.get("year")
    if requested_year_id:
        selected_year = AccountingYear.objects.filter(company=company, pk=requested_year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(AccountingYear.objects.filter(company=company))

    preview_data = None
    preview_key = sie_preview_session_key(request.user.id, company.id, selected_year.id)

    if request.method == "POST" and request.POST.get("action") == "confirm":
        form = SIImportForm(company=company)
        preview_payload = request.session.get(preview_key)

        if not preview_payload:
            messages.error(request, "Importunderlag saknas. Ladda upp SI-filen igen.")
            return redirect(f"{reverse('bookkeeping:si_import')}?year={selected_year.pk}")

        serialized_verifications = preview_payload.get("verifications") or []
        verifications = deserialize_sie_verifications(serialized_verifications)
        duplicate_map = find_likely_duplicate_verifications(verifications, selected_year)

        account_mapping = {}
        included_verifications = {}
        mapping_errors = []

        for ver_idx, ver in enumerate(verifications):
            include_verification = request.POST.get(f"include_ver_{ver_idx}", "1") == "1"
            included_verifications[ver_idx] = include_verification
            if not include_verification:
                continue

            for entry_idx, entry in enumerate(ver.get("entries", [])):
                source_number = entry["account"]
                target_number = (request.POST.get(f"account_map_{ver_idx}_{entry_idx}") or "").strip()
                if not target_number:
                    mapping_errors.append(f"Målkonto saknas för rad med importkonto {source_number}.")
                    continue
                if not target_number.isdigit() or len(target_number) > 10:
                    mapping_errors.append(
                        f"Målkonto {target_number} för importkonto {source_number} är ogiltigt. Använd 1-10 siffror."
                    )
                    continue
                account_mapping[(ver_idx, entry_idx)] = target_number

        if mapping_errors:
            for error_message in mapping_errors:
                messages.error(request, error_message)
            preview_data = build_si_verification_preview(
                verifications,
                company,
                posted_mapping=account_mapping,
                duplicate_map=duplicate_map,
            )
            return render(
                request,
                "bookkeeping/si_import.html",
                {
                    "form": form,
                    "selected_year": selected_year,
                    "preview_data": preview_data,
                    "verification_count": len(verifications),
                    "duplicate_warning_count": len(duplicate_map),
                },
            )

        imported = 0
        omitted = 0
        skipped_outside_year = 0
        skipped_unbalanced = 0
        next_si_number = next_si_series_number(selected_year)

        with db_transaction.atomic():
            with audit_source("SI-import"):
                for ver_idx, ver in enumerate(verifications):
                    if not included_verifications.get(ver_idx, True):
                        omitted += 1
                        continue

                    ver_date = ver["date"]
                    if not (selected_year.start_date <= ver_date <= selected_year.end_date):
                        skipped_outside_year += 1
                        continue
                    if is_date_locked(company, ver_date):
                        omitted += 1
                        continue

                    entries = ver.get("entries", [])
                    if len(entries) < 2:
                        skipped_unbalanced += 1
                        continue

                    total_debit = Decimal("0")
                    total_credit = Decimal("0")
                    for item in entries:
                        amount = item["amount"]
                        if amount >= 0:
                            total_debit += amount
                        else:
                            total_credit += -amount

                    if total_debit != total_credit:
                        skipped_unbalanced += 1
                        continue

                    reference = f"{SI_REFERENCE_PREFIX} {next_si_number}"
                    next_si_number += 1

                    txn = Transaction.objects.create(
                        accounting_year=selected_year,
                        date=ver_date,
                        description=ver["description"] or f"Importerad verifikation {reference}",
                        reference=reference,
                        created_by=request.user,
                        source=TransactionSource.SIE_IMPORT,
                    )

                    for entry_idx, item in enumerate(entries):
                        source_account_number = item["account"]
                        target_account_number = account_mapping.get((ver_idx, entry_idx), source_account_number)
                        account = Account.objects.filter(company=company, number=target_account_number).first()
                        if account is None:
                            account_class = (
                                target_account_number[0]
                                if target_account_number and target_account_number[0] in "123456789"
                                else "9"
                            )
                            account = Account.objects.create(
                                company=company,
                                number=target_account_number,
                                name=f"Importerat konto {target_account_number}",
                                account_class=account_class,
                                is_active=True,
                            )

                        amount = item["amount"]
                        debit = amount if amount > 0 else Decimal("0")
                        credit = -amount if amount < 0 else Decimal("0")

                        JournalEntry.objects.create(
                            transaction=txn,
                            account=account,
                            debit=debit,
                            credit=credit,
                            description="",
                        )

                    txn.validate_balanced()
                    imported += 1

        request.session.pop(preview_key, None)
        messages.success(
            request,
            (
                f"SI-import klar. Importerade: {imported}, "
                f"Utelämnade: {omitted}, "
                f"Utanför räkenskapsår: {skipped_outside_year}, "
                f"Obalanserade: {skipped_unbalanced}."
            ),
        )
        return redirect(f"{reverse('bookkeeping:transaction_list')}?year={selected_year.pk}")

    elif request.method == "POST":
        form = SIImportForm(request.POST, request.FILES, company=company)
        if form.is_valid():
            upload = form.cleaned_data["si_file"]
            payload = upload.read()

            text = None
            for encoding in ("utf-8-sig", "cp437", "latin-1"):
                try:
                    text = payload.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                messages.error(request, "Kunde inte läsa SI-filen. Kontrollera filkodning.")
                return render(
                    request,
                    "bookkeeping/si_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                    },
                )

            sie_type = None
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line.upper().startswith("#SIETYP"):
                    continue
                parts = line.split()
                if len(parts) > 1:
                    sie_type = parts[1].strip('"').upper()
                break

            if sie_type and sie_type not in {"4", "4I"}:
                messages.error(
                    request,
                    f"SI/SIE-typen {sie_type} stöds inte för verifikationsimport. Endast typ 4/4I stöds.",
                )
                return render(
                    request,
                    "bookkeeping/si_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                    },
                )

            verifications, diagnostics = parse_sie_verifications_with_diagnostics(text)
            if not verifications:
                messages.error(request, "Ogiltig SI-fil: inga tolkbara verifikationer hittades.")
                if diagnostics:
                    messages.warning(request, f"Detaljer: {summarize_import_diagnostics(diagnostics)}")
                return render(
                    request,
                    "bookkeeping/si_import.html",
                    {
                        "form": form,
                        "selected_year": selected_year,
                    },
                )

            request.session[preview_key] = {
                "company_id": company.id,
                "year_id": selected_year.id,
                "verifications": serialize_sie_verifications(verifications),
                "diagnostics": diagnostics,
            }
            request.session.modified = True

            duplicate_map = find_likely_duplicate_verifications(verifications, selected_year)
            preview_data = build_si_verification_preview(verifications, company, duplicate_map=duplicate_map)
            messages.info(
                request,
                "Kontrollera och justera kontomappningen innan du importerar verifikationerna.",
            )
            if diagnostics:
                messages.warning(
                    request,
                    (
                        f"Importvalidering hittade {len(diagnostics)} radfel i filen. "
                        f"{summarize_import_diagnostics(diagnostics)}"
                    ),
                )
        else:
            duplicate_map = {}
            preview_data = None
    else:
        form = SIImportForm(company=company)
        duplicate_map = {}
        preview_data = None

    return render(
        request,
        "bookkeeping/si_import.html",
        {
            "form": form,
            "selected_year": selected_year,
            "preview_data": preview_data,
            "verification_count": len((request.session.get(preview_key) or {}).get("verifications") or []),
            "duplicate_warning_count": len(duplicate_map),
            "diagnostics_available": bool((request.session.get(preview_key) or {}).get("diagnostics")),
        },
    )


@login_required
@require_compliance_action("import.sie")
@company_required
def si_import_diagnostics_download(request, company):

    selected_year = None
    requested_year_id = request.GET.get("year")
    if requested_year_id:
        selected_year = AccountingYear.objects.filter(company=company, pk=requested_year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(AccountingYear.objects.filter(company=company))
    if selected_year is None:
        messages.error(request, "Inget räkenskapsår valt.")
        return redirect("bookkeeping:accounting_year_list")

    preview_key = sie_preview_session_key(request.user.id, company.id, selected_year.id)
    diagnostics = (request.session.get(preview_key) or {}).get("diagnostics") or []
    if not diagnostics:
        messages.error(request, "Ingen SI-diagnostik finns sparad ännu. Ladda upp en fil först.")
        return redirect(f"{reverse('bookkeeping:si_import')}?year={selected_year.pk}")

    payload = serialize_import_diagnostics_payload("SI", diagnostics)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="si-import-diagnostics-{selected_year.ending_year}.json"'
    return response
