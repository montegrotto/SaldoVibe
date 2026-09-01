"""Bokföringsexport: a self-contained, browsable zip bundle for a freely chosen date range.

Phase 1 + 2 + 3 cover five sections, tied together by a small standalone HTML site
(`templates/bookkeeping/export_bundle_site/`) and a `manifest.json` with a SHA256 checksum per
file (per `docs/compliance/audit-export-spec.md`):

- `bokforingsdata/` — complete SIE4 files, one per räkenskapsår overlapping the period.
- `kontospecifikationer/` — one PDF per account with any posting in the period: opening
  balance, every posting, running balance, closing balance.
- `bilagor/` — every attachment relevant to the period, split into `bokforda/` (linked to a
  voucher/registered document booked within the period) and `ej-bokforda/` (not yet booked,
  uploaded within the period).
- `banktransaktioner/` — one CSV per bank/skattekonto källa with any transaction in the period,
  as reported by the bank rather than as booked — so an auditor can reconcile the bank's own
  record against the ledger, including any transaction that arrived but was never booked.
- `rapporter/` — regenerated PDFs of moms/AGI/lönebesked, but only for periods that were
  actually reported/closed within the export range: `moms/` (one PDF per `VatCloseSnapshot`,
  i.e. a momsperiod that was stängd, not every period that merely overlaps the range),
  `agi/` (one PDF per `PayrollRun` already marked `is_reported_to_skatteverket`) and
  `lonebesked/` (one PDF per `SalaryRecord` on a *finished* payroll run). An unclosed/unreported
  period has no fixed figures yet, so including it would misrepresent it as filed.

Every function here is a pure builder: company/dates in, `BundleFile`s or bytes out. No
request/response handling — that lives in `bookkeeping/views/export.py`.

`generate_export_bundle` itself can take a while (SIE + one PDF per account + reading every
attachment's file), so it never runs inside a request/response cycle — `start_export_job` records
an `ExportJob` row and hands the actual work to `run_export_job` on a background thread; the view
returns immediately and the job's status/file live in the DB/filesystem, not in the thread, so any
later request (even one served by a different gunicorn worker process) can see the result.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import mimetypes
import threading
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from attachments.models import TransactionAttachment

from .models import Account, AccountingYear, ExportJob, JournalEntry, Transaction
from .pdf import company_logo_size, render_pdf_bytes
from .sie import _account_net_amounts_by_id, encode_sie, generate_sie4_content

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "saldovibe.export.v1"


@dataclass(frozen=True)
class BundleFile:
    path: str
    content: bytes
    content_type: str
    description: str


def _safe_filename(value: str) -> str:
    return (value or "").replace("/", "-").replace("\\", "-").strip()


def _content_type_for(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _voucher_reference(transaction) -> str:
    return transaction.reference or f"{transaction.voucher_series}{transaction.voucher_number or ''}"


def overlapping_accounting_years(company, start_date, end_date):
    """Every AccountingYear whose date range overlaps [start_date, end_date]."""
    return AccountingYear.objects.filter(company=company, start_date__lte=end_date, end_date__gte=start_date).order_by(
        "start_date"
    )


def document_bucket_querysets(company, start_date, end_date):
    """(booked_qs, not_booked_qs): attachments relevant to the export period.

    `booked_qs` is linked to a voucher/registered document whose booking date falls in range.
    `not_booked_qs` is genuinely not booked to anything (yet) and was uploaded in range — an
    attachment that *is* booked, just outside the chosen range, appears in neither bucket.
    """
    truly_booked_qs = TransactionAttachment.objects.filter(company=company, deleted_at__isnull=True).filter(
        Q(transactions__isnull=False)
        | Q(supplier_invoices__is_registered=True)
        | Q(sales_invoices__is_booked=True)
        | Q(expense_claims__is_registered=True)
    )

    booked_qs = truly_booked_qs.filter(
        Q(transactions__date__range=(start_date, end_date))
        | Q(
            supplier_invoices__is_registered=True,
            supplier_invoices__registered_transaction__date__range=(start_date, end_date),
        )
        | Q(
            sales_invoices__is_booked=True,
            sales_invoices__booked_transaction__date__range=(start_date, end_date),
        )
        | Q(
            expense_claims__is_registered=True,
            expense_claims__registered_transaction__date__range=(start_date, end_date),
        )
    ).distinct()

    not_booked_qs = TransactionAttachment.objects.filter(
        company=company, deleted_at__isnull=True, uploaded_at__date__range=(start_date, end_date)
    ).exclude(pk__in=truly_booked_qs.values("pk"))

    return booked_qs, not_booked_qs


def _classify_attachment(attachment) -> dict:
    """Which business document this attachment belongs to, and how to describe it."""
    transaction = attachment.transactions.order_by("date").first()
    if transaction is not None:
        return {
            "booked": True,
            "booked_date": transaction.date,
            "type_label": "Verifikation",
            "status_label": "",
            "description": transaction.description,
            "amount": transaction.total_debit,
            "reference": _voucher_reference(transaction),
        }

    supplier_invoice = attachment.supplier_invoices.first()
    if supplier_invoice is not None:
        booked = supplier_invoice.is_registered and supplier_invoice.registered_transaction_id is not None
        return {
            "booked": booked,
            "booked_date": supplier_invoice.registered_transaction.date if booked else None,
            "type_label": "Leverantörsfaktura",
            "status_label": "Betald" if supplier_invoice.is_paid else "Obetald",
            "description": supplier_invoice.supplier_name,
            "amount": supplier_invoice.settled_total,
            "reference": _voucher_reference(supplier_invoice.registered_transaction) if booked else "",
        }

    sales_invoice = attachment.sales_invoices.first()
    if sales_invoice is not None:
        booked = sales_invoice.is_booked and sales_invoice.booked_transaction_id is not None
        return {
            "booked": booked,
            "booked_date": sales_invoice.booked_transaction.date if booked else None,
            "type_label": "Kundfaktura",
            "status_label": "Betald" if sales_invoice.is_paid else "Obetald",
            "description": sales_invoice.customer.name,
            "amount": sales_invoice.settled_total,
            "reference": _voucher_reference(sales_invoice.booked_transaction) if booked else "",
        }

    expense_claim = attachment.expense_claims.first()
    if expense_claim is not None:
        booked = expense_claim.is_registered and expense_claim.registered_transaction_id is not None
        return {
            "booked": booked,
            "booked_date": expense_claim.registered_transaction.date if booked else None,
            "type_label": "Utlägg",
            "status_label": "Betald" if expense_claim.is_paid else "Obetald",
            "description": expense_claim.description,
            "amount": expense_claim.settled_total,
            "reference": _voucher_reference(expense_claim.registered_transaction) if booked else "",
        }

    return {
        "booked": False,
        "booked_date": None,
        "type_label": "Bilaga",
        "status_label": "",
        "description": attachment.file_name,
        "amount": None,
        "reference": "",
    }


def build_ledger_files(company, start_date, end_date) -> list[BundleFile]:
    years = overlapping_accounting_years(company, start_date, end_date)

    files: list[BundleFile] = []
    rows = []
    for year in years:
        transactions = Transaction.objects.filter(accounting_year=year).prefetch_related("entries__account")
        sie_content = generate_sie4_content(company, year, transactions)
        payload = encode_sie(sie_content)
        filename = f"SIE4_{year.start_date:%Y%m%d}-{year.end_date:%Y%m%d}.se"
        files.append(
            BundleFile(
                f"bokforingsdata/{filename}",
                payload,
                "text/plain",
                f"SIE4-export för räkenskapsåret {year.start_date} – {year.end_date}",
            )
        )
        rows.append({"year": year, "filename": filename})

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/ledger_index.html",
        {"company": company, "rows": rows, "css_path": "../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile("bokforingsdata/index.html", index_html.encode("utf-8"), "text/html", "Lista över bokföringsdata")
    )
    return files


def compute_account_specifications(company, start_date, end_date) -> list[dict]:
    """Per account with any posting in range: opening balance, each posting with a running
    balance, and the closing balance. Pure data, no PDF rendering — kept separate from
    `build_account_specification_files` so the arithmetic is testable without xhtml2pdf."""
    account_ids_with_activity = (
        JournalEntry.objects.filter(account__company=company, transaction__date__range=(start_date, end_date))
        .values_list("account_id", flat=True)
        .distinct()
    )
    accounts = list(Account.objects.filter(company=company, id__in=account_ids_with_activity).order_by("number"))
    if not accounts:
        return []

    account_ids = [account.pk for account in accounts]
    day_before_start = start_date - timedelta(days=1)
    opening_balances = _account_net_amounts_by_id(account_ids, company=company, upto_date=day_before_start)

    entries_by_account = defaultdict(list)
    entries_qs = (
        JournalEntry.objects.filter(account_id__in=account_ids, transaction__date__range=(start_date, end_date))
        .select_related("transaction")
        .order_by("transaction__date", "transaction__voucher_number", "id")
    )
    for entry in entries_qs:
        entries_by_account[entry.account_id].append(entry)

    specifications = []
    for account in accounts:
        opening_balance = opening_balances.get(account.pk, Decimal("0"))
        running_balance = opening_balance
        postings = []
        for entry in entries_by_account.get(account.pk, []):
            running_balance += (entry.debit or Decimal("0")) - (entry.credit or Decimal("0"))
            transaction = entry.transaction
            postings.append(
                {
                    "date": transaction.date,
                    "reference": _voucher_reference(transaction),
                    "description": entry.description or transaction.description,
                    "debit": entry.debit,
                    "credit": entry.credit,
                    "balance": running_balance,
                }
            )
        specifications.append(
            {
                "account": account,
                "opening_balance": opening_balance,
                "postings": postings,
                "closing_balance": running_balance,
            }
        )
    return specifications


def build_account_specification_files(company, start_date, end_date) -> list[BundleFile]:
    specifications = compute_account_specifications(company, start_date, end_date)

    files: list[BundleFile] = []
    index_rows = []
    for spec in specifications:
        account = spec["account"]
        pdf_bytes = render_pdf_bytes(
            "bookkeeping/export_bundle_site/account_specification_pdf.html",
            {
                "company": company,
                "logo_size": company_logo_size(company),
                "account": account,
                "start_date": start_date,
                "end_date": end_date,
                "opening_balance": spec["opening_balance"],
                "postings": spec["postings"],
                "closing_balance": spec["closing_balance"],
            },
        )
        filename = f"Konto {account.number} {_safe_filename(account.name)}.pdf"
        files.append(
            BundleFile(
                f"kontospecifikationer/{filename}",
                pdf_bytes,
                "application/pdf",
                f"Kontospecifikation för konto {account.number} {account.name}",
            )
        )
        index_rows.append(
            {
                "account": account,
                "filename": filename,
                "opening_balance": spec["opening_balance"],
                "closing_balance": spec["closing_balance"],
            }
        )

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/account_specification_index.html",
        {"company": company, "rows": index_rows, "css_path": "../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile(
            "kontospecifikationer/index.html",
            index_html.encode("utf-8"),
            "text/html",
            "Lista över kontospecifikationer",
        )
    )
    return files


def build_document_files(company, start_date, end_date) -> tuple[list[BundleFile], list[dict]]:
    booked_qs, not_booked_qs = document_bucket_querysets(company, start_date, end_date)
    prefetch = (
        "transactions",
        "supplier_invoices__registered_transaction",
        "sales_invoices__customer",
        "sales_invoices__booked_transaction",
        "expense_claims__registered_transaction",
    )
    attachments = list(booked_qs.prefetch_related(*prefetch)) + list(not_booked_qs.prefetch_related(*prefetch))
    attachments.sort(key=lambda attachment: attachment.uploaded_at)

    files: list[BundleFile] = []
    rows = []
    for sequence, attachment in enumerate(attachments, start=1):
        info = _classify_attachment(attachment)
        bucket = "bokforda" if info["booked"] else "ej-bokforda"
        reference_part = _safe_filename(info["reference"])
        prefix = f"D{sequence}" + (f"_{reference_part}" if reference_part else "")
        archive_name = f"{prefix}_{_safe_filename(attachment.file_name)}"

        attachment.file.open("rb")
        try:
            content = attachment.file.read()
        finally:
            attachment.file.close()

        files.append(
            BundleFile(
                f"bilagor/{bucket}/{archive_name}",
                content,
                _content_type_for(attachment.file_name),
                f"Bilaga D{sequence}: {info['description']}",
            )
        )
        rows.append(
            {
                "sequence": sequence,
                "bucket": bucket,
                "archive_name": archive_name,
                "uploaded_at": attachment.uploaded_at,
                **info,
            }
        )

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/documents_index.html",
        {"company": company, "rows": rows, "css_path": "../css/style.css", "back_href": "../index.html"},
    )
    files.append(BundleFile("bilagor/index.html", index_html.encode("utf-8"), "text/html", "Lista över bilagor"))
    return files, rows


def bank_transaction_period_queryset(company, start_date, end_date):
    """BankTransaction rows dated within the period, across every bank/skattekonto källa.

    Local import: `banking` imports `bookkeeping` models, so importing `banking.models` at module
    load time here would be circular — see the same pattern in
    `bookkeeping/views/transactions.py`.
    """
    from banking.models import BankTransaction

    return BankTransaction.objects.filter(company=company, date__range=(start_date, end_date))


def bank_transaction_counts(company, start_date, end_date) -> dict:
    """Cheap aggregate counts for the export preview, without building any CSV."""
    qs = bank_transaction_period_queryset(company, start_date, end_date)
    return {
        "account_count": qs.values("bank_account_id").distinct().count(),
        "transaction_count": qs.count(),
        "not_booked_count": qs.filter(is_booked=False).count(),
    }


def build_bank_transaction_files(company, start_date, end_date) -> list[BundleFile]:
    """One CSV per bank/skattekonto källa with any transaction in the period, plus an HTML index.

    This is the bank's own record, not the ledger's: it includes transactions that were never
    booked, and its amounts/balances aren't cross-checked against `bokforingsdata/` here — that
    reconciliation is left to the auditor, which is the point of shipping both sections.
    """
    from banking.models import BankAccount

    transactions = list(
        bank_transaction_period_queryset(company, start_date, end_date)
        .select_related("bank_account", "booked_transaction")
        .order_by("bank_account__name", "date", "id")
    )
    transactions_by_account_id = defaultdict(list)
    for bank_tx in transactions:
        transactions_by_account_id[bank_tx.bank_account_id].append(bank_tx)

    bank_accounts = BankAccount.objects.filter(pk__in=transactions_by_account_id.keys()).order_by("name")

    files: list[BundleFile] = []
    rows = []
    for bank_account in bank_accounts:
        account_transactions = transactions_by_account_id[bank_account.pk]

        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["Datum", "Text", "Belopp", "Saldo", "Bokförd", "Verifikation"])
        booked_count = 0
        for bank_tx in account_transactions:
            if bank_tx.is_booked:
                booked_count += 1
            writer.writerow(
                [
                    bank_tx.date.isoformat(),
                    bank_tx.description,
                    f"{bank_tx.amount:.2f}",
                    f"{bank_tx.balance:.2f}" if bank_tx.balance is not None else "",
                    "Ja" if bank_tx.is_booked else "Nej",
                    _voucher_reference(bank_tx.booked_transaction) if bank_tx.booked_transaction_id else "",
                ]
            )

        filename = f"{_safe_filename(bank_account.name)}.csv"
        files.append(
            BundleFile(
                f"banktransaktioner/{filename}",
                buffer.getvalue().encode("utf-8-sig"),
                "text/csv",
                f"Banktransaktioner för {bank_account.name} ({bank_account.get_account_type_display()})",
            )
        )
        rows.append(
            {
                "bank_account": bank_account,
                "filename": filename,
                "transaction_count": len(account_transactions),
                "booked_count": booked_count,
                "not_booked_count": len(account_transactions) - booked_count,
            }
        )

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/bank_transactions_index.html",
        {"company": company, "rows": rows, "css_path": "../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile(
            "banktransaktioner/index.html", index_html.encode("utf-8"), "text/html", "Lista över banktransaktioner"
        )
    )
    return files


def vat_close_snapshot_queryset(company, start_date, end_date):
    """VatCloseSnapshot rows for momsperioder stängda within the period.

    Local import: `vat` imports `bookkeeping` models, so importing `vat.models` at module load
    time here would be circular — see the same pattern in `bank_transaction_period_queryset`.
    """
    from vat.models import VatCloseSnapshot

    return VatCloseSnapshot.objects.filter(
        company=company, period_start__lte=end_date, period_end__gte=start_date
    ).order_by("period_start")


def payroll_run_queryset(company, start_date, end_date):
    """PayrollRun rows paid within the period. Local import: see `vat_close_snapshot_queryset`."""
    from payroll.models import PayrollRun

    return PayrollRun.objects.filter(company=company, payment_date__range=(start_date, end_date)).order_by(
        "payment_date"
    )


def reports_counts(company, start_date, end_date) -> dict:
    """Cheap aggregate counts for the export preview, without rendering any PDF."""
    from payroll.models import SalaryRecord

    payroll_runs = payroll_run_queryset(company, start_date, end_date)
    return {
        "vat_period_count": vat_close_snapshot_queryset(company, start_date, end_date).count(),
        "agi_run_count": payroll_runs.filter(is_reported_to_skatteverket=True).count(),
        "payslip_count": SalaryRecord.objects.filter(payroll_run__in=payroll_runs.filter(is_finished=True)).count(),
    }


def build_vat_report_files(company, start_date, end_date) -> list[BundleFile]:
    """One PDF per momsperiod stängd (`VatCloseSnapshot`) within the range — an ongoing/unclosed
    period has no fixed figures yet, so it's excluded rather than regenerated on the fly."""
    from vat.services import get_skatteverket_field_groups

    snapshots = list(vat_close_snapshot_queryset(company, start_date, end_date).select_related("closed_by"))

    files: list[BundleFile] = []
    rows = []
    for snapshot in snapshots:
        vat_boxes = {code: Decimal(value) for code, value in snapshot.vat_boxes.items()}
        field_groups = get_skatteverket_field_groups(vat_boxes)
        pdf_bytes = render_pdf_bytes(
            "bookkeeping/export_bundle_site/vat_report_pdf.html",
            {
                "company": company,
                "logo_size": company_logo_size(company),
                "period_start": snapshot.period_start,
                "period_end": snapshot.period_end,
                "field_groups": field_groups,
                "closed_at": snapshot.closed_at,
                "closed_by": snapshot.closed_by,
            },
        )
        filename = f"Moms {snapshot.period_start:%Y-%m-%d} - {snapshot.period_end:%Y-%m-%d}.pdf"
        files.append(
            BundleFile(
                f"rapporter/moms/{filename}",
                pdf_bytes,
                "application/pdf",
                f"Momsdeklaration för perioden {snapshot.period_start} – {snapshot.period_end}",
            )
        )
        rows.append({"snapshot": snapshot, "filename": filename})

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/vat_report_index.html",
        {"company": company, "rows": rows, "css_path": "../../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile("rapporter/moms/index.html", index_html.encode("utf-8"), "text/html", "Lista över momsdeklarationer")
    )
    return files


def build_agi_report_files(company, start_date, end_date) -> list[BundleFile]:
    """One PDF per `PayrollRun` already reported to Skatteverket within the range — an
    unreported run has no confirmed AGI-underlag yet."""
    from payroll.models import ensure_payroll_report_evidence

    payroll_runs = list(
        payroll_run_queryset(company, start_date, end_date)
        .filter(is_reported_to_skatteverket=True)
        .prefetch_related("salary_records__employee")
    )

    files: list[BundleFile] = []
    rows = []
    for payroll_run in payroll_runs:
        evidence = ensure_payroll_report_evidence(payroll_run)
        salary_records = list(
            payroll_run.salary_records.select_related("employee").order_by(
                "employee__personal_identity_number_hash", "id"
            )
        )
        totals = {
            "gross_salary": sum((r.gross_salary for r in salary_records), Decimal("0.00")),
            "preliminary_tax_amount": sum((r.preliminary_tax_amount for r in salary_records), Decimal("0.00")),
            "employer_contribution_amount": sum(
                (r.employer_contribution_amount for r in salary_records), Decimal("0.00")
            ),
            "net_salary": sum((r.net_salary for r in salary_records), Decimal("0.00")),
        }
        pdf_bytes = render_pdf_bytes(
            "bookkeeping/export_bundle_site/agi_report_pdf.html",
            {
                "company": company,
                "logo_size": company_logo_size(company),
                "payroll_run": payroll_run,
                "salary_records": salary_records,
                "totals": totals,
                "evidence": evidence,
            },
        )
        filename = f"AGI {payroll_run.period_year}-{payroll_run.period_month:02d}.pdf"
        files.append(
            BundleFile(
                f"rapporter/agi/{filename}",
                pdf_bytes,
                "application/pdf",
                f"AGI-underlag för {payroll_run.period_label} {payroll_run.period_year}",
            )
        )
        rows.append({"payroll_run": payroll_run, "filename": filename, "evidence": evidence})

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/agi_report_index.html",
        {"company": company, "rows": rows, "css_path": "../../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile("rapporter/agi/index.html", index_html.encode("utf-8"), "text/html", "Lista över AGI-underlag")
    )
    return files


def build_payslip_files(company, start_date, end_date) -> list[BundleFile]:
    """One PDF per `SalaryRecord` on a finished payroll run paid within the range — an
    unfinished run's figures can still change, so its payslips aren't final yet."""
    from payroll.models import SalaryRecord
    from payroll.views import salary_report_pdf_context

    salary_records = list(
        SalaryRecord.objects.filter(
            payroll_run__company=company,
            payroll_run__is_finished=True,
            payroll_run__payment_date__range=(start_date, end_date),
        )
        .select_related("payroll_run__company", "employee")
        .prefetch_related("adjustments")
        .order_by("payroll_run__payment_date", "employee__first_name", "employee__last_name")
    )

    files: list[BundleFile] = []
    rows = []
    for salary_record in salary_records:
        payroll_run = salary_record.payroll_run
        pdf_bytes = render_pdf_bytes(
            "payroll/salary_report_print.html",
            salary_report_pdf_context(salary_record),
        )
        filename = (
            f"{_safe_filename(str(salary_record.employee))} "
            f"{payroll_run.period_year}-{payroll_run.period_month:02d}.pdf"
        )
        files.append(
            BundleFile(
                f"rapporter/lonebesked/{filename}",
                pdf_bytes,
                "application/pdf",
                f"Lönebesked {salary_record.employee} – {payroll_run.period_label}",
            )
        )
        rows.append({"salary_record": salary_record, "filename": filename})

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/payslip_index.html",
        {"company": company, "rows": rows, "css_path": "../../css/style.css", "back_href": "../index.html"},
    )
    files.append(
        BundleFile("rapporter/lonebesked/index.html", index_html.encode("utf-8"), "text/html", "Lista över lönebesked")
    )
    return files


def build_reports_files(company, start_date, end_date) -> list[BundleFile]:
    """`rapporter/`: regenerated moms-, AGI- and lönebesked-PDF:er for the period (Phase 3 of
    R-013 in SKATTEVERKET_COMPLIANCE_PLAN.md)."""
    vat_files = build_vat_report_files(company, start_date, end_date)
    agi_files = build_agi_report_files(company, start_date, end_date)
    payslip_files = build_payslip_files(company, start_date, end_date)

    files = [*vat_files, *agi_files, *payslip_files]

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/reports_index.html",
        {"company": company, "css_path": "../css/style.css", "back_href": "../index.html"},
    )
    files.append(BundleFile("rapporter/index.html", index_html.encode("utf-8"), "text/html", "Lista över rapporter"))
    return files


def build_export_manifest(company, start_date, end_date, files: list[BundleFile], *, generated_at) -> dict:
    """Per docs/compliance/audit-export-spec.md: every payload file gets a SHA256 checksum."""
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "company": {"name": company.name, "org_number": company.org_number},
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "files": [
            {
                "path": bundle_file.path,
                "sha256": hashlib.sha256(bundle_file.content).hexdigest(),
                "content_type": bundle_file.content_type,
                "description": bundle_file.description,
            }
            for bundle_file in files
        ],
    }


EXPORT_BUNDLE_CSS = """
body { font-family: Arial, Helvetica, sans-serif; color: #2f3a44; margin: 0; padding: 0 0 2rem 0; background: #f8fafc; }
nav.top { background: #1d4ed8; padding: 1rem 1.5rem; }
nav.top h1 { color: #fff; margin: 0; font-size: 1.25rem; }
nav.top h2 { color: #dbeafe; margin: 0; font-weight: normal; font-size: 1rem; }
main { max-width: 1100px; margin: 1.5rem auto; padding: 0 1.5rem; }
h3 { margin-top: 0; }
p.subinfo { color: #64748b; font-size: 0.9rem; }
.box { border: 1px solid #d9e2ec; background: #fff; border-radius: 6px; }
.button { display: inline-block; background: #1d4ed8; color: #fff; padding: 0.35rem 0.9rem; border-radius: 4px; text-decoration: none; font-size: 0.85rem; }
.button.right { float: right; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; background: #fff; }
table td, table th { padding: 0.6rem 0.9rem; text-align: left; border: 1px solid #e2e8f0; }
table th { background: #eef2ff; color: #334155; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 3px; font-size: 0.75rem; margin-right: 0.15rem; }
.badge-success { background: #dcfce7; color: #166534; }
.badge-danger { background: #fee2e2; color: #991b1b; }
.badge-primary { background: #e0e7ff; color: #3730a3; }
.amount { text-align: right; white-space: nowrap; }
"""


def generate_export_bundle(company, start_date, end_date) -> bytes:
    generated_at = timezone.now()

    ledger_files = build_ledger_files(company, start_date, end_date)
    account_specification_files = build_account_specification_files(company, start_date, end_date)
    document_files, _document_rows = build_document_files(company, start_date, end_date)
    bank_transaction_files = build_bank_transaction_files(company, start_date, end_date)
    report_files = build_reports_files(company, start_date, end_date)

    files = [
        *ledger_files,
        *account_specification_files,
        *document_files,
        *bank_transaction_files,
        *report_files,
    ]

    index_html = render_to_string(
        "bookkeeping/export_bundle_site/index.html",
        {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "generated_at": generated_at,
            "css_path": "css/style.css",
            "back_href": "",
        },
    )
    files.append(BundleFile("index.html", index_html.encode("utf-8"), "text/html", "Exportöversikt"))
    files.append(
        BundleFile("css/style.css", EXPORT_BUNDLE_CSS.encode("utf-8"), "text/css", "Stilmall för exportsajten")
    )

    manifest = build_export_manifest(company, start_date, end_date, files, generated_at=generated_at)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for bundle_file in files:
            zf.writestr(bundle_file.path, bundle_file.content)
        zf.writestr("manifest.json", manifest_bytes)
    return buf.getvalue()


def run_export_job(job_id) -> None:
    """Generate the bundle for `job_id` and persist it. Runs on a background thread — re-fetches
    the job by id rather than taking a model instance, since a Django model/connection must not be
    shared across threads."""
    job = ExportJob.objects.select_related("company").get(pk=job_id)
    job.status = ExportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])

    try:
        zip_bytes = generate_export_bundle(job.company, job.start_date, job.end_date)
    except Exception as exc:
        # A background thread's exception has no request/response to surface it to - this is
        # the one place that must catch everything, or the job would stay "running" forever.
        job.status = ExportJob.Status.FAILED
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])
        logger.exception("Bokföringsexport misslyckades (job %s)", job_id)
        return

    org_nr = (job.company.org_number or "").replace("-", "").replace(" ", "") or f"company-{job.company.pk}"
    filename = f"saldovibe-export_{org_nr}_{job.start_date:%Y-%m-%d}-{job.end_date:%Y-%m-%d}.zip"
    job.file.save(filename, ContentFile(zip_bytes), save=False)
    job.status = ExportJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.save(update_fields=["file", "status", "completed_at"])


def start_export_job(company, start_date, end_date, requested_by) -> ExportJob:
    """Create the job row (committed immediately, so the new thread's own DB connection can see
    it) and hand the actual work to a background thread. Returns right away."""
    job = ExportJob.objects.create(
        company=company,
        requested_by=requested_by,
        start_date=start_date,
        end_date=end_date,
        status=ExportJob.Status.PENDING,
    )
    threading.Thread(target=run_export_job, args=(job.pk,), daemon=True).start()
    return job
