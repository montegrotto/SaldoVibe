from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from bookkeeping.company_scope import require_company
from bookkeeping.compliance_policy import require_compliance_action
from bookkeeping.models import AccountingYear, Company, JournalEntry, Transaction, TransactionSource
from bookkeeping.period_locking import is_date_locked, is_range_locked
from bookkeeping.reports import default_accounting_year

from .models import VatCloseSnapshot
from .services import (
    SKATTEVERKET_FIELD_CODES,
    SKATTEVERKET_FIELD_LABELS,
    VAT_CLOSING_REFERENCE_PREFIX,
    build_closed_periods,
    build_vat_closing_reference,
    build_vat_periods,
    build_vat_source_fingerprint,
    calculate_vat_boxes,
    get_field_account_prefixes,
    get_field_account_query,
    get_skatteverket_field_groups,
    get_transaction_field_amount,
    get_vat_closing_balances,
    parse_period_key,
    validate_eskd_export,
    whole_krona_vat_boxes,
)

ESKD_UPLOAD_PUBLIC_ID = "-//Skatteverket, Sweden//DTD Skatteverket eSKDUpload-DTD Version 6.0//SV"
ESKD_UPLOAD_SYSTEM_ID = "https://www1.skatteverket.se/demoeskd/eSKDUpload_6p0.dtd"


def _vat_reporting_enabled(company):
    return company.vat_reporting_period != Company.VatReportingPeriod.NONE


def _effective_vat_start_date(company, period_start_date):
    if company.vat_start_date and company.vat_start_date > period_start_date:
        return company.vat_start_date
    return period_start_date


def _get_selected_period_or_redirect(company, year_id, period_key):
    selected_year = AccountingYear.objects.filter(company=company, pk=year_id).first()
    if selected_year is None:
        return None, None, redirect("vat:report"), "Ogiltigt rakenskapsår för momsrapport."

    periods = build_closed_periods(
        selected_year,
        company.vat_reporting_period,
        today=date.today(),
        vat_start_date=company.vat_start_date,
    )
    period_map = {period["key"]: period for period in periods}
    selected_period = period_map.get(period_key)
    if selected_period is not None:
        return selected_year, selected_period, None, None

    start_date, end_date = parse_period_key(period_key)
    if not start_date or not end_date:
        return selected_year, None, redirect(reverse("vat:report") + f"?year={selected_year.pk}"), "Ogiltig momsperiod."

    return (
        selected_year,
        None,
        redirect(reverse("vat:report") + f"?year={selected_year.pk}"),
        "Momsperioden är inte avslutad och kan inte rapporteras ännu.",
    )


def _build_eskd_moms_xml(company, selected_period, vat_boxes):
    period_value = selected_period["end_date"].strftime("%Y%m")

    boxes = whole_krona_vat_boxes(vat_boxes)
    values = {
        "ForsMomsEjAnnan": boxes["05"],
        "UttagMoms": boxes["06"],
        "UlagMargbesk": boxes["07"],
        "HyrinkomstFriv": boxes["08"],
        "InkopVaruAnnatEg": boxes["20"],
        "InkopTjanstAnnatEg": boxes["21"],
        "InkopTjanstUtomEg": boxes["22"],
        "InkopVaruSverige": boxes["23"],
        "InkopTjanstSverige": boxes["24"],
        "MomsUlagImport": 0,
        "ForsVaruAnnatEg": boxes["35"],
        "ForsVaruUtomEg": boxes["36"],
        "InkopVaruMellan3p": boxes["37"],
        "ForsVaruMellan3p": 0,
        "ForsTjSkskAnnatEg": boxes["38"],
        "ForsTjOvrUtomEg": boxes["39"],
        "ForsKopareSkskSverige": 0,
        "ForsOvrigt": 0,
        "MomsUtgHog": boxes["10"],
        "MomsUtgMedel": boxes["11"],
        "MomsUtgLag": boxes["12"],
        "MomsInkopUtgHog": boxes["30"],
        "MomsInkopUtgMedel": boxes["31"],
        "MomsInkopUtgLag": boxes["32"],
        "MomsImportUtgHog": 0,
        "MomsImportUtgMedel": 0,
        "MomsImportUtgLag": 0,
        "MomsIngAvdr": boxes["48"],
        # eSKDUpload 6.0 has no separate box-50 element: MomsBetala is signed, negative
        # when VAT is to be refunded.
        "MomsBetala": boxes["49"] - boxes["50"],
    }

    lines = [
        '<?xml version="1.0" encoding="ISO-8859-1"?>',
        (f'<!DOCTYPE eSKDUpload PUBLIC "{ESKD_UPLOAD_PUBLIC_ID}" "{ESKD_UPLOAD_SYSTEM_ID}">'),
        '<eSKDUpload Version="6.0">',
        f"  <OrgNr>{escape((company.org_number or '').strip())}</OrgNr>",
        "  <Moms>",
        f"    <Period>{period_value}</Period>",
    ]

    for key in [
        "ForsMomsEjAnnan",
        "UttagMoms",
        "UlagMargbesk",
        "HyrinkomstFriv",
        "InkopVaruAnnatEg",
        "InkopTjanstAnnatEg",
        "InkopTjanstUtomEg",
        "InkopVaruSverige",
        "InkopTjanstSverige",
        "MomsUlagImport",
        "ForsVaruAnnatEg",
        "ForsVaruUtomEg",
        "InkopVaruMellan3p",
        "ForsVaruMellan3p",
        "ForsTjSkskAnnatEg",
        "ForsTjOvrUtomEg",
        "ForsKopareSkskSverige",
        "ForsOvrigt",
        "MomsUtgHog",
        "MomsUtgMedel",
        "MomsUtgLag",
        "MomsInkopUtgHog",
        "MomsInkopUtgMedel",
        "MomsInkopUtgLag",
        "MomsImportUtgHog",
        "MomsImportUtgMedel",
        "MomsImportUtgLag",
        "MomsIngAvdr",
        "MomsBetala",
    ]:
        lines.append(f"    <{key}>{values[key]}</{key}>")

    lines.extend(
        [
            "    <TextUpplysningMoms></TextUpplysningMoms>",
            "  </Moms>",
            "</eSKDUpload>",
            "",
        ]
    )

    return "\n".join(lines)


def _annotate_period_report_status(company, selected_year, periods):
    if not periods:
        return periods

    existing_refs = set(
        Transaction.objects.filter(
            accounting_year=selected_year,
            reference__startswith=VAT_CLOSING_REFERENCE_PREFIX,
        ).values_list("reference", flat=True)
    )

    for period in periods:
        effective_start_date = _effective_vat_start_date(company, period["start_date"])
        closing_reference = build_vat_closing_reference(effective_start_date, period["end_date"])
        period["is_reported"] = closing_reference in existing_refs

    return periods


def _sort_periods_for_overview(periods):
    def sort_key(period):
        if period["status"] == "closed" and not period["is_reported"]:
            group = 0
        elif period["status"] == "ongoing":
            group = 1
        else:
            group = 2
        return (group, -period["start_date"].toordinal())

    return sorted(periods, key=sort_key)


def _resolve_year_and_period(request, company):
    years = AccountingYear.objects.filter(company=company).order_by("-start_date", "-id")
    if company.vat_start_date:
        years = years.exclude(end_date__lt=company.vat_start_date)
    selected_year = None
    year_id = request.GET.get("year")
    if year_id:
        selected_year = years.filter(pk=year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(years)

    periods = []
    selected_period = None

    if selected_year is not None:
        periods = build_vat_periods(
            selected_year,
            company.vat_reporting_period,
            today=date.today(),
            vat_start_date=company.vat_start_date,
        )
        periods = _sort_periods_for_overview(_annotate_period_report_status(company, selected_year, periods))

    selected_key = request.GET.get("period")
    if periods:
        period_map = {period["key"]: period for period in periods}
        selected_period = period_map.get(selected_key) if selected_key else periods[0]

    return years, selected_year, periods, selected_period


@login_required
@require_company
def vat_report(request, company):

    if not _vat_reporting_enabled(company):
        messages.info(request, "Momsrapportering är avstängd för aktivt företag.")
        return redirect("bookkeeping:dashboard")

    years, selected_year, periods, selected_period = _resolve_year_and_period(request, company)

    vat_boxes = None
    field_groups = []
    validation_status = {"errors": [], "warnings": []}

    if selected_period:
        effective_start_date = _effective_vat_start_date(company, selected_period["start_date"])
        vat_boxes = calculate_vat_boxes(
            company=company,
            start_date=effective_start_date,
            end_date=selected_period["end_date"],
        )
        field_groups = get_skatteverket_field_groups(vat_boxes)
        validation_status = validate_eskd_export(
            company=company,
            start_date=effective_start_date,
            end_date=selected_period["end_date"],
            vat_boxes=vat_boxes,
        )
        closing_reference = build_vat_closing_reference(effective_start_date, selected_period["end_date"])
        closing_transaction = Transaction.objects.filter(
            accounting_year__company=company,
            reference=closing_reference,
        ).first()
    else:
        closing_transaction = None

    return render(
        request,
        "vat/report.html",
        {
            "years": years,
            "selected_year": selected_year,
            "periods": periods,
            "selected_period": selected_period,
            "field_groups": field_groups,
            "validation_status": validation_status,
            "closing_transaction": closing_transaction,
            "field_codes_with_transactions": set(
                code
                for code, prefixes in {
                    code: get_field_account_prefixes(code) for code in SKATTEVERKET_FIELD_CODES
                }.items()
                if prefixes
            ),
        },
    )


@login_required
@require_compliance_action("vat.close_period")
@require_company
def vat_export_skatteverket(request, company):

    if not _vat_reporting_enabled(company):
        messages.error(request, "Momsrapportering är avstängd för aktivt företag.")
        return redirect("bookkeeping:dashboard")

    year_id = request.GET.get("year")
    period_key = request.GET.get("period")
    if not year_id or not period_key:
        messages.error(request, "Valj rakenskapsar och period innan export.")
        return redirect("vat:report")

    selected_year, selected_period, redirect_response, error_message = _get_selected_period_or_redirect(
        company,
        year_id,
        period_key,
    )
    if redirect_response is not None:
        messages.error(request, error_message)
        return redirect_response

    effective_start_date = _effective_vat_start_date(company, selected_period["start_date"])

    vat_boxes = calculate_vat_boxes(
        company=company,
        start_date=effective_start_date,
        end_date=selected_period["end_date"],
    )

    validation_result = validate_eskd_export(
        company=company,
        start_date=effective_start_date,
        end_date=selected_period["end_date"],
        vat_boxes=vat_boxes,
    )
    for warning in validation_result["warnings"]:
        messages.warning(request, warning)

    if validation_result["errors"]:
        for error_message in validation_result["errors"]:
            messages.error(request, error_message)
        return redirect(reverse("vat:report") + f"?year={selected_year.pk}&period={period_key}")

    response = HttpResponse(content_type="application/xml; charset=iso-8859-1")
    filename = f"eskd_moms_{company.org_number or company.pk}_{selected_period['end_date'].strftime('%Y%m')}.xml"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    response.write(_build_eskd_moms_xml(company=company, selected_period=selected_period, vat_boxes=vat_boxes))

    return response


@login_required
@require_compliance_action("vat.close_period")
@require_POST
@require_company
def vat_close_period(request, company):

    if not _vat_reporting_enabled(company):
        messages.error(request, "Momsrapportering är avstängd för aktivt företag.")
        return redirect("bookkeeping:dashboard")

    year_id = request.POST.get("year")
    period_key = request.POST.get("period")
    if not year_id or not period_key:
        messages.error(request, "Välj räkenskapsår och period innan du stänger momsperioden.")
        return redirect("vat:report")

    selected_year, selected_period, redirect_response, error_message = _get_selected_period_or_redirect(
        company,
        year_id,
        period_key,
    )
    if redirect_response is not None:
        messages.error(request, error_message)
        return redirect_response

    effective_start_date = _effective_vat_start_date(company, selected_period["start_date"])
    closing_reference = build_vat_closing_reference(effective_start_date, selected_period["end_date"])
    redirect_url = reverse("vat:report") + f"?year={selected_year.pk}&period={period_key}"

    if Transaction.objects.filter(accounting_year__company=company, reference=closing_reference).exists():
        messages.info(request, "Momsperioden är redan stängd.")
        return redirect(redirect_url)

    if is_date_locked(company, selected_period["end_date"]):
        messages.error(
            request,
            "Perioden för stängningsdatumet är låst. Lås upp perioden innan momsperioden stängs.",
        )
        return redirect(redirect_url)

    settlement_account = company.accounts.filter(number="2650", is_active=True).first()
    if settlement_account is None:
        messages.error(request, "Kontot 2650 saknas eller är inaktivt. Lägg upp kontot innan momsperioden stängs.")
        return redirect(redirect_url)

    closing_balances = get_vat_closing_balances(
        company=company,
        start_date=effective_start_date,
        end_date=selected_period["end_date"],
    )
    if not closing_balances:
        messages.info(request, "Det finns inga momsbalanser att stänga för vald period.")
        return redirect(redirect_url)

    vat_boxes = calculate_vat_boxes(
        company=company,
        start_date=effective_start_date,
        end_date=selected_period["end_date"],
    )
    # 2650 tar det deklarerade heltalsbeloppet (ruta 49/50, det som debiteras/krediteras
    # skattekontot); öresskillnaden mot de bokförda momssaldona går till 3740.
    declared = whole_krona_vat_boxes(vat_boxes)
    declared_net = Decimal(declared["49"] - declared["50"])
    booked_net = sum(-row["balance"] if row["field_code"] == "48" else row["balance"] for row in closing_balances)
    rounding_difference = declared_net - booked_net
    rounding_account = company.accounts.filter(number="3740", is_active=True).first()
    if rounding_difference and rounding_account is None:
        messages.error(
            request,
            "Kontot 3740 saknas eller är inaktivt. Lägg upp kontot för öresutjämning innan momsperioden stängs.",
        )
        return redirect(redirect_url)

    with db_transaction.atomic():
        # Lås räkenskapsåret så två samtidiga "stäng period" inte skapar dubbla stängningsverifikationer.
        AccountingYear.objects.select_for_update().get(pk=selected_year.pk)
        if Transaction.objects.filter(accounting_year__company=company, reference=closing_reference).exists():
            messages.info(request, "Momsperioden är redan stängd.")
            return redirect(redirect_url)

        closing_transaction = Transaction.objects.create(
            accounting_year=selected_year,
            date=selected_period["end_date"],
            description=f"Stängning av momsperiod {selected_period['label']}",
            reference=closing_reference,
            created_by=request.user,
            source=TransactionSource.VAT,
        )

        for balance_row in closing_balances:
            account = company.accounts.get(pk=balance_row["account_id"])
            amount = balance_row["balance"]
            debit = Decimal("0.00")
            credit = Decimal("0.00")

            if balance_row["field_code"] == "48":
                amount = -amount
            if amount > 0:
                debit = amount
            else:
                credit = -amount

            JournalEntry.objects.create(
                transaction=closing_transaction,
                account=account,
                debit=debit,
                credit=credit,
                description="Stängning av momsperiod",
            )

        if rounding_difference:
            JournalEntry.objects.create(
                transaction=closing_transaction,
                account=rounding_account,
                debit=max(rounding_difference, Decimal("0.00")),
                credit=max(-rounding_difference, Decimal("0.00")),
                description="Öresutjämning momsperiod",
            )

        if declared_net:
            JournalEntry.objects.create(
                transaction=closing_transaction,
                account=settlement_account,
                debit=max(-declared_net, Decimal("0.00")),
                credit=max(declared_net, Decimal("0.00")),
                description="Motkonto momsperiod",
            )

        closing_transaction.validate_balanced()

        source_fingerprint, source_transaction_ids = build_vat_source_fingerprint(
            company=company,
            start_date=effective_start_date,
            end_date=selected_period["end_date"],
        )

        VatCloseSnapshot.objects.update_or_create(
            company=company,
            accounting_year=selected_year,
            period_start=effective_start_date,
            period_end=selected_period["end_date"],
            defaults={
                "closed_transaction": closing_transaction,
                "vat_boxes": {k: format(v, "f") for k, v in vat_boxes.items()},
                "source_transaction_ids": source_transaction_ids,
                "source_fingerprint": source_fingerprint,
                "closed_by": request.user,
            },
        )

    messages.success(request, "Momsperioden har stängts och bokförts mot konto 2650.")
    if not is_range_locked(company, effective_start_date, selected_period["end_date"]):
        messages.info(
            request,
            "Momsperioden är inte periodlåst. Lås den under Inställningar → Periodlåsning "
            "så att underlaget för momsdeklarationen inte kan ändras i efterhand.",
        )
    return redirect(redirect_url)


@login_required
@require_company
def vat_field_transactions(request, company, field_code):

    if not _vat_reporting_enabled(company):
        messages.info(request, "Momsrapportering är avstängd för aktivt företag.")
        return redirect("bookkeeping:dashboard")

    if field_code not in SKATTEVERKET_FIELD_CODES:
        messages.error(request, "Ogiltig fältkod.")
        return redirect("vat:report")

    years, selected_year, periods, selected_period = _resolve_year_and_period(request, company)
    if selected_year is None or selected_period is None:
        messages.error(request, "Välj en avslutad momsperiod för att se verifikationer.")
        return redirect("vat:report")

    transactions = Transaction.objects.none()
    account_filter = get_field_account_query(field_code)
    if account_filter.children:
        effective_start_date = _effective_vat_start_date(company, selected_period["start_date"])
        transactions = (
            Transaction.objects.filter(
                accounting_year__company=company,
                date__gte=effective_start_date,
                date__lte=selected_period["end_date"],
            )
            .filter(account_filter)
            .exclude(reference__startswith=VAT_CLOSING_REFERENCE_PREFIX)
            .distinct()
            .prefetch_related("entries__account")
            .order_by("-date", "-created_at")
        )
        for txn in transactions:
            txn.field_amount = get_transaction_field_amount(txn.entries.all(), field_code)

    return render(
        request,
        "vat/field_transactions.html",
        {
            "field_code": field_code,
            "field_label": SKATTEVERKET_FIELD_LABELS.get(field_code, ""),
            "transactions": transactions,
            "selected_year": selected_year,
            "selected_period": selected_period,
            "periods": periods,
            "years": years,
            "return_to": request.get_full_path(),
        },
    )
