"""SRU export for Skatteverket, with its pre-flight validation report."""

import csv
import json
import logging
import re
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from ..compliance_policy import require_compliance_action
from ..models import (
    Account,
    AccountingYear,
    JournalEntry,
)
from ..reports import default_accounting_year
from ._base import company_required

logger = logging.getLogger(__name__)

SRU_CODE_RE = re.compile(r"^\d{4}$")


def _build_sru_preflight(company, accounting_year):
    accounts_with_entries = (
        Account.objects.filter(
            company=company,
            journalentry__transaction__accounting_year=accounting_year,
        )
        .distinct()
        .order_by("number")
    )

    missing_code_accounts = []
    invalid_code_accounts = []
    for account in accounts_with_entries:
        sru_code = (account.sru_code or "").strip()
        if not sru_code:
            missing_code_accounts.append(account)
            continue
        if not SRU_CODE_RE.match(sru_code):
            invalid_code_accounts.append(account)

    errors = []
    if missing_code_accounts:
        errors.append("SRU-export blockerad: minst ett använt konto saknar SRU-kod.")
    if invalid_code_accounts:
        errors.append("SRU-export blockerad: minst ett konto har ogiltig SRU-kod (måste vara fyra siffror).")

    return {
        "errors": errors,
        "missing_code_accounts": missing_code_accounts,
        "invalid_code_accounts": invalid_code_accounts,
    }


@login_required
@company_required
def sru_report(request, company):

    years = AccountingYear.objects.filter(company=company).order_by("-start_date")
    selected_year = None
    year_id = request.GET.get("year")
    if year_id:
        selected_year = years.filter(pk=year_id).first()
    if selected_year is None:
        selected_year = default_accounting_year(years)

    sru_rows = []
    preflight = {"errors": [], "missing_code_accounts": [], "invalid_code_accounts": []}
    if selected_year:
        preflight = _build_sru_preflight(company, selected_year)
        accounts_with_sru = Account.objects.filter(company=company, sru_code__gt="").order_by("sru_code", "number")
        # Aggregate debit/credit per account within the selected year
        entries_qs = (
            JournalEntry.objects.filter(
                transaction__accounting_year=selected_year,
                account__in=accounts_with_sru,
            )
            .values("account_id")
            .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
        )
        balances = {
            row["account_id"]: (row["total_debit"] or Decimal("0"), row["total_credit"] or Decimal("0"))
            for row in entries_qs
        }

        # Group by SRU code
        sru_groups = defaultdict(lambda: {"accounts": [], "net": Decimal("0")})
        for account in accounts_with_sru:
            debit, credit = balances.get(account.pk, (Decimal("0"), Decimal("0")))
            net = debit - credit
            if not net:
                continue
            sru_groups[account.sru_code]["accounts"].append(
                {
                    "account": account,
                    "debit": debit,
                    "credit": credit,
                    "net": net,
                }
            )
            sru_groups[account.sru_code]["net"] += net

        sru_rows = sorted(sru_groups.items(), key=lambda x: x[0])

    return render(
        request,
        "bookkeeping/sru_report.html",
        {
            "years": years,
            "selected_year": selected_year,
            "sru_rows": sru_rows,
            "preflight": preflight,
        },
    )


@login_required
@require_compliance_action("export.sru")
@company_required
def sru_download(request, company):
    import io
    import zipfile

    year_id = request.GET.get("year")
    years = AccountingYear.objects.filter(company=company)
    selected_year = years.filter(pk=year_id).first() if year_id else default_accounting_year(years)
    if selected_year is None:
        messages.error(request, "Inget räkenskapsår valt.")
        return redirect("bookkeeping:sru_report")

    preflight = _build_sru_preflight(company, selected_year)
    if preflight["errors"]:
        messages.error(request, "SRU-export stoppades av valideringsfel. Korrigera SRU-koder innan export.")
        return redirect(f"{reverse('bookkeeping:sru_report')}?year={selected_year.pk}")

    accounts_with_sru = Account.objects.filter(company=company, sru_code__gt="").order_by("sru_code", "number")
    entries_qs = (
        JournalEntry.objects.filter(
            transaction__accounting_year=selected_year,
            account__in=accounts_with_sru,
        )
        .values("account_id")
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    )
    balances = {
        row["account_id"]: (row["total_debit"] or Decimal("0"), row["total_credit"] or Decimal("0"))
        for row in entries_qs
    }

    # Aggregate net per SRU code (rounded to whole SEK as per Skatteverket spec)
    sru_totals = defaultdict(Decimal)
    for account in accounts_with_sru:
        debit, credit = balances.get(account.pk, (Decimal("0"), Decimal("0")))
        sru_totals[account.sru_code] += debit - credit

    if not sru_totals:
        messages.error(request, "SRU-export kunde inte skapas eftersom inga konton med SRU-koder har saldo i perioden.")
        return redirect(f"{reverse('bookkeeping:sru_report')}?year={selected_year.pk}")

    # Goes straight into #ORGNR and #IDENTITET. An empty value produces a file
    # Skatteverket will reject, so refuse here instead of shipping one.
    org_nr = (company.org_number or "").replace("-", "").replace(" ", "")
    if not org_nr:
        messages.error(request, "SRU-export kräver att företagets organisationsnummer är ifyllt.")
        return redirect(f"{reverse('bookkeeping:sru_report')}?year={selected_year.pk}")

    end_date_str = selected_year.end_date.strftime("%Y%m%d")
    year_label = selected_year.end_date.strftime("%Y")

    # ---- info.sru ----
    info_lines = [
        "#DATABESKRIVNING_START",
        "#PRODUKT SRU",
        "#FILNAMN blanketter.sru",
        "#DATABESKRIVNING_SLUT",
        "#MEDIELEV_START",
        f"#ORGNR {org_nr}",
        f"#NAMN {company.name}",
    ]
    if company.address:
        info_lines.append(f"#ADRESS {company.address}")
    info_lines.append("#MEDIELEV_SLUT")
    info_content = "\r\n".join(info_lines) + "\r\n"

    # ---- blanketter.sru ----
    blankett_lines = [
        f"#BLANKETT INK2-{year_label}P4",
        f"#IDENTITET {org_nr} {end_date_str}",
        f"#NAMN {company.name}",
    ]
    for sru_code, net in sorted(sru_totals.items()):
        amount = int(net.quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
        blankett_lines.append(f"#UPPGIFT {sru_code} {amount}")
    blankett_lines += ["#BLANKETTSLUT", "#FIL_SLUT"]
    blankett_content = "\r\n".join(blankett_lines) + "\r\n"

    # Pack into zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("info.sru", info_content.encode("cp437", errors="replace"))
        zf.writestr("blanketter.sru", blankett_content.encode("cp437", errors="replace"))
    buf.seek(0)

    filename = f"SRU_{org_nr}_{year_label}.zip"
    response = HttpResponse(buf, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_compliance_action("export.sru")
@company_required
def sru_preflight_report_download(request, company):

    year_id = request.GET.get("year")
    years = AccountingYear.objects.filter(company=company)
    selected_year = years.filter(pk=year_id).first() if year_id else default_accounting_year(years)
    if selected_year is None:
        messages.error(request, "Inget räkenskapsår valt.")
        return redirect("bookkeeping:sru_report")

    preflight = _build_sru_preflight(company, selected_year)
    report_rows = []
    for account in preflight["missing_code_accounts"]:
        report_rows.append(
            {
                "severity": "error",
                "type": "missing_sru_code",
                "account": account.number,
                "name": account.name,
                "value": "",
            }
        )
    for account in preflight["invalid_code_accounts"]:
        report_rows.append(
            {
                "severity": "error",
                "type": "invalid_sru_code",
                "account": account.number,
                "name": account.name,
                "value": account.sru_code,
            }
        )

    fmt = (request.GET.get("format") or "json").lower()
    if fmt == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="sru-preflight-{selected_year.ending_year}.csv"'
        writer = csv.writer(response, delimiter=";")
        writer.writerow(["severity", "type", "account", "name", "value"])
        for row in report_rows:
            writer.writerow([row["severity"], row["type"], row["account"], row["name"], row["value"]])
        return response

    payload = {
        "company_id": company.id,
        "accounting_year_id": selected_year.id,
        "error_count": len(preflight["errors"]),
        "errors": preflight["errors"],
        "findings": report_rows,
        "generated_at": timezone.now().isoformat(),
    }
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8"
    )
    response["Content-Disposition"] = f'attachment; filename="sru-preflight-{selected_year.ending_year}.json"'
    return response
