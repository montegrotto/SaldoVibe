"""Notiskällor för "något behöver uppmärksamhet".

Delas av topbarens notisklocka (bookkeeping/context_processors.py) och den
dagliga e-postdigesten (skicka_notisdigest). Varje källa returnerar
(lines, count, signature): svenska textrader för digesten, antal för klockan
och en signatur som avgör när en kvitterad klocka ska tändas igen.
"""

import datetime

from django.utils import timezone

from .models import SentEmail

VAT_DEADLINE_WARNING_DAYS = 14


def get_overdue_customer_invoice_state(company, today):
    from invoicing.models import Invoice

    # is_credit_invoice är en property (negativ totalsumma), inte ett fält — filtrera i Python.
    invoices = [
        invoice
        for invoice in Invoice.objects.filter(
            company=company,
            is_booked=True,
            is_paid=False,
            due_date__lt=today,
        )
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("pk")
        if not invoice.is_credit_invoice
    ]
    lines = [
        f"Faktura {invoice.invoice_number or invoice.pk} till {invoice.customer.name} förföll {invoice.due_date}."
        for invoice in invoices
    ]
    signature = "|".join(str(invoice.pk) for invoice in invoices)
    return lines, len(invoices), signature


def get_overdue_supplier_invoice_state(company, today):
    from supplier_invoices.models import SupplierInvoice

    invoices = list(
        SupplierInvoice.objects.filter(company=company, is_paid=False, due_date__lt=today)
        .select_related("supplier")
        .order_by("pk")
    )
    lines = [f"Leverantörsfaktura från {invoice.supplier.name} förföll {invoice.due_date}." for invoice in invoices]
    signature = "|".join(str(invoice.pk) for invoice in invoices)
    return lines, len(invoices), signature


def _vat_filing_deadline(period_end):
    # ponytail: 12:e i andra månaden efter periodslut för alla periodtyper;
    # årsmoms har egentligen egna datum (26:e/inkomstdeklarationen) — förfina om årsmomsbolag klagar.
    year = period_end.year + (period_end.month + 2 - 1) // 12
    month = (period_end.month + 2 - 1) % 12 + 1
    return datetime.date(year, month, 12)


def get_vat_deadline_state(company, today):
    from vat.models import VatCloseSnapshot
    from vat.services import build_closed_periods

    if company.vat_reporting_period in ("", "none"):
        return [], 0, ""

    lines = []
    keys = []
    # Äldre år än så kan inte ha en aktuell deadline inom varningsfönstret.
    years = company.accounting_years.filter(end_date__gte=today - datetime.timedelta(days=460)).order_by("start_date")
    for year in years:
        declared = {
            (start, end)
            for start, end in VatCloseSnapshot.objects.filter(company=company, accounting_year=year).values_list(
                "period_start", "period_end"
            )
        }
        for period in build_closed_periods(year, company.vat_reporting_period, today, company.vat_start_date):
            if (period["start_date"], period["end_date"]) in declared:
                continue
            deadline = _vat_filing_deadline(period["end_date"])
            if deadline > today + datetime.timedelta(days=VAT_DEADLINE_WARNING_DAYS):
                continue
            when = "förföll" if deadline < today else "ska lämnas senast"
            lines.append(f"Momsdeklarationen för {period['label']} {when} {deadline}.")
            keys.append(period["key"])
    return lines, len(lines), "|".join(keys)


def get_failed_job_state(company, today):
    # Digestutskick räknas inte: en trasig system-SMTP skulle annars göra
    # varje efterföljande digest icke-tom för evigt.
    failed = list(
        SentEmail.objects.filter(
            company=company,
            status=SentEmail.Status.FAILED,
            created_at__gte=timezone.now() - datetime.timedelta(days=3),
        )
        .exclude(purpose=SentEmail.Purpose.DIGEST)
        .order_by("pk")
    )
    lines = [
        f"E-post ({sent.get_purpose_display()}) till {sent.recipient} kunde inte skickas {timezone.localdate(sent.created_at)}."
        for sent in failed
    ]
    keys = [str(sent.pk) for sent in failed]
    if company.email_fetch_last_error:
        lines.append(f"E-postimporten av bilagor misslyckades: {company.email_fetch_last_error[:200]}")
        keys.append(
            f"fetch:{company.email_fetch_last_error_at.isoformat() if company.email_fetch_last_error_at else ''}"
        )
    return lines, len(lines), "|".join(keys)
