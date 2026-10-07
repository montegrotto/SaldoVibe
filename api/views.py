"""JSON endpoints behind the iPhone app (ios/).

Token auth and company scoping live in auth.py. Every write goes through the same forms
as the web views (ExpenseClaimForm, MileageReportForm, MileageReport.submit), so the two clients
cannot disagree on validation or default accounts. The app never posts to the ledger: expenses and
körrapporter are saved as drafts (the app picks an ExpenseCategory, whose account is the
suggestion) and are booked and paid on the web. Supplier and customer invoices are read-only.
"""

from decimal import Decimal

from django.contrib.auth import authenticate
from django.db import transaction as db_transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.models import ApiToken
from attachments.models import TransactionAttachment
from attachments.services import save_uploaded_attachment
from attachments.utils import exclude_used_attachments
from attachments.view_helpers import selectable_attachments
from attachments.views import attachment_file_response, attachment_thumbnail_response, uploaded_file_from_request
from bookkeeping.company_scope import get_user_companies, is_read_only_member
from bookkeeping.context_processors import get_topbar_alert_state_for_company
from bookkeeping.payables import quantize_amount
from bookkeeping.reports import build_balance_sheet_context, build_income_statement_context
from bookkeeping.views.dashboard import dashboard_summary
from expenses.forms import ExpenseClaimForm
from expenses.models import ExpenseClaim
from invoicing.models import Invoice
from payroll.forms import MileageReportForm
from payroll.models import MileageReport
from supplier_invoices.models import SupplierInvoice

from .auth import ApiError, api_view, form_data, form_error, json_body

LIST_LIMIT = 200

# Same wording as templates/bookkeeping/_topbar_alert_bell.html. `target` is the app tab
# that shows the alert's documents; None means "handled on the web".
ALERTS = (
    ("overdue_supplier_invoices_count", "Förfallna leverantörsfakturor", "supplier_invoices"),
    ("supplier_invoices_due_soon_count", "Leverantörsfakturor förfaller inom 3 dagar", "supplier_invoices"),
    ("overdue_customer_invoices_count", "Förfallna kundfakturor", "customer_invoices"),
    ("vat_deadlines_count", "Momsdeklaration att lämna", None),
    ("payroll_runs_due_soon_count", "Löneutbetalningar inom 3 dagar", None),
    ("recurring_invoices_due_count", "Återkommande fakturor redo att genereras", None),
    ("fixed_assets_due_count", "Anläggningstillgångar att hantera", None),
    ("export_jobs_ready_count", "Exportpaket redo att laddas ner", None),
    ("failed_email_jobs_count", "Misslyckade e-postjobb", None),
)


# --- serialisers -----------------------------------------------------------------------


def _amount(value):
    return str(quantize_amount(value or Decimal("0.00")))


def _date(value):
    return value.isoformat() if value else None


def _datetime(value):
    return timezone.localtime(value).isoformat(timespec="seconds") if value else None


def _int(value):
    return int(value) if str(value).isdigit() else None


def _account_json(account):
    if account is None:
        return None
    return {"id": account.pk, "number": account.number, "name": account.name, "account_class": account.account_class}


def _attachment_json(attachment):
    return {
        "id": attachment.pk,
        "file_name": attachment.file_name,
        "uploaded_at": _datetime(attachment.uploaded_at),
        "is_pdf": attachment.file_name.lower().endswith(".pdf"),
        "suggestion": attachment.extracted_data or None,
    }


def _payable_json(document, **fields):
    badge = document.payment_status_badge
    return {
        "id": document.pk,
        "total_amount": _amount(document.total_amount),
        "paid_amount": _amount(document.paid_amount),
        "remaining_amount": _amount(document.remaining_amount),
        "is_paid": document.is_paid,
        "is_bookkept": document.is_bookkept,
        "status": badge["key"],
        "status_label": badge["label"],
        "payment_date": _date(document.payment_date),
        **fields,
    }


def _with_attachments(data, document):
    data["attachments"] = [_attachment_json(a) for a in document.attachments.filter(deleted_at__isnull=True)]
    return data


def _expense_json(claim, detail=False):
    data = _payable_json(
        claim,
        description=claim.description,
        expense_date=_date(claim.expense_date),
        person=claim.person_display_name,
        employee_id=claim.employee_id,
        vat_amount=_amount(claim.vat_amount),
        amount_ex_vat=_amount(claim.amount_ex_vat),
        expense_account=_account_json(claim.expense_account),
        category=claim.category.name if claim.category_id else None,
    )
    if not detail:
        return data
    report = getattr(claim, "mileage_report", None)
    data["mileage"] = (
        {
            "route": report.route,
            "purpose": report.purpose,
            "trip_date": _date(report.trip_date),
            "distance_km": str(report.distance_km),
            "rate_per_mil": _amount(report.rate_per_mil),
        }
        if report
        else None
    )
    return _with_attachments(data, claim)


def _supplier_invoice_json(invoice, detail=False):
    data = _payable_json(
        invoice,
        supplier_name=invoice.supplier_display_name,
        supplier_id=invoice.supplier_id,
        invoice_number=invoice.invoice_number,
        ocr_code=invoice.ocr_code,
        invoice_date=_date(invoice.invoice_date),
        due_date=_date(invoice.due_date),
        is_overdue=not invoice.is_paid and invoice.due_date < timezone.localdate(),
        vat_amount=_amount(invoice.vat_amount),
        amount_ex_vat=_amount(invoice.amount_ex_vat),
    )
    if detail:
        data["cost_lines"] = [
            {"account": _account_json(line.expense_account), "amount": _amount(line.debit - line.credit)}
            for line in invoice.cost_lines.select_related("expense_account")
        ]
        _with_attachments(data, invoice)
    return data


def _customer_invoice_json(invoice, detail=False):
    data = _payable_json(
        invoice,
        customer_name=invoice.customer.name,
        invoice_number=invoice.invoice_number,
        ocr_code=invoice.ocr_code,
        invoice_date=_date(invoice.invoice_date),
        due_date=_date(invoice.due_date),
        is_overdue=invoice.is_booked
        and not invoice.is_paid
        and not invoice.is_credit_invoice
        and invoice.due_date < timezone.localdate(),
        is_credit_invoice=invoice.is_credit_invoice,
        vat_amount=_amount(invoice.vat_amount),
        amount_ex_vat=_amount(invoice.subtotal_ex_vat),
    )
    if detail:
        data["lines"] = [
            {
                "description": line.description,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "unit_price": _amount(line.unit_price),
                "vat_rate": str(line.vat_rate),
                "total_ex_vat": _amount(line.line_total_ex_vat),
            }
            for line in invoice.item_lines
        ]
        _with_attachments(data, invoice)
    return data


def _me(user):
    return {
        "user": {"email": user.email, "name": user.get_full_name()},
        "companies": [
            {
                "id": company.pk,
                "name": company.name,
                "vat_registered": company.vat_registered,
                "read_only": is_read_only_member(user, company),
            }
            for company in get_user_companies(user)
        ],
    }


# --- auth ------------------------------------------------------------------------------


@api_view(("POST",), auth=False)
def login(request):
    data = json_body(request)
    user = authenticate(
        request, username=str(data.get("email") or "").strip(), password=str(data.get("password") or "")
    )
    if user is None:
        raise ApiError("Fel e-postadress eller lösenord.")
    token = ApiToken.issue(user, name=str(data.get("device_name") or ""))
    return JsonResponse({"token": token, **_me(user)})


@api_view(("POST",), company=False)
def logout(request):
    request.api_token.delete()
    return HttpResponse(status=204)


@api_view(company=False)
def me(request):
    return JsonResponse(_me(request.user))


# --- overview --------------------------------------------------------------------------


@api_view()
def overview(request, company):
    today = timezone.localdate()
    summary = dashboard_summary(company, today)
    state = get_topbar_alert_state_for_company(company)
    year = summary["current_year"]
    open_supplier = list(SupplierInvoice.objects.filter(company=company, is_paid=False))
    open_customer = [
        invoice
        for invoice in Invoice.objects.filter(company=company, is_booked=True, is_paid=False).prefetch_related("lines")
        if not invoice.is_credit_invoice
    ]
    return JsonResponse(
        {
            "company": {"id": company.pk, "name": company.name},
            "accounting_year": {"start_date": _date(year.start_date), "end_date": _date(year.end_date)}
            if year
            else None,
            "revenue": _amount(summary["rev_total"]),
            "costs": _amount(summary["cost_total"]),
            "net_result": _amount(summary["net_result"]),
            "cash_balance": _amount(summary["cash_balance"]),
            "bank_accounts": [
                {"number": row["account"].number, "name": row["account"].name, "balance": _amount(row["balance"])}
                for row in summary["account_balances"]
            ],
            "payables_open": {
                "count": len(open_supplier),
                "total": _amount(sum((i.remaining_amount for i in open_supplier), Decimal("0.00"))),
            },
            "receivables_open": {
                "count": len(open_customer),
                "total": _amount(sum((i.remaining_amount for i in open_customer), Decimal("0.00"))),
            },
            "alerts": [
                {"key": key, "text": text, "count": state[key], "target": target}
                for key, text, target in ALERTS
                if state[key]
            ],
        }
    )


@api_view()
def form_choices(request, company):
    """Everything the app's forms need to offer in pickers, in one round trip."""
    return JsonResponse(
        {
            "vat_registered": company.vat_registered,
            "expense_categories": [{"id": c.pk, "name": c.name} for c in company.expense_categories.order_by("name")],
            "mileage_rate_per_mil": _amount(MileageReport.DEFAULT_RATE_PER_MIL),
            "employees": [
                {"id": e.pk, "name": str(e)}
                for e in company.employees.filter(is_active=True).order_by("first_name", "last_name")
            ],
        }
    )


# --- attachments -----------------------------------------------------------------------


def _attachment(company, pk):
    return get_object_or_404(TransactionAttachment, pk=pk, company=company, deleted_at__isnull=True)


def _link_attachments(document, company, data):
    ids = [int(value) for value in data.get("attachment_ids") or [] if str(value).isdigit()]
    attachments = selectable_attachments(company, ids)
    if attachments.exists():
        document.attachments.add(*attachments)


@api_view(("GET", "POST"))
def attachments(request, company):
    """Unlinked attachments (what the web's Bilagor list shows), or upload one (multipart
    field `file`; PDF, PNG or JPEG). The upload answer carries ReInvGrabber's suggestion
    so the app can prefill an expense straight away."""
    if request.method == "POST":
        uploaded = uploaded_file_from_request(request)
        if uploaded is None:
            raise ApiError("Ingen fil togs emot. Skicka filen som formulärfältet file (PDF, PNG eller JPEG).")
        attachment = save_uploaded_attachment(uploaded, company=company, user=request.user)
        return JsonResponse(_attachment_json(attachment), status=201)
    queryset = exclude_used_attachments(
        TransactionAttachment.objects.filter(company=company, deleted_at__isnull=True)
    ).order_by("-uploaded_at")
    return JsonResponse([_attachment_json(a) for a in queryset[:LIST_LIMIT]], safe=False)


@api_view()
def attachment_file(request, company, pk):
    return attachment_file_response(_attachment(company, pk))


@api_view()
def attachment_thumbnail(request, company, pk):
    return attachment_thumbnail_response(_attachment(company, pk))


# --- expenses --------------------------------------------------------------------------


def _expense(company, pk):
    return get_object_or_404(
        ExpenseClaim.objects.select_related("employee", "expense_account", "category"), pk=pk, company=company
    )


@api_view(("GET", "POST"))
def expenses(request, company):
    if request.method == "POST":
        data = json_body(request)
        with db_transaction.atomic():
            form = ExpenseClaimForm(form_data(data), company=company)
            if not form.is_valid():
                raise form_error(form)
            claim = form.save(commit=False)
            claim.company = company
            claim.created_by = request.user
            if claim.employee_id:
                claim.person_name = str(claim.employee)
            claim.save()
            _link_attachments(claim, company, data)
        return JsonResponse(_expense_json(claim, detail=True), status=201)
    claims = ExpenseClaim.objects.filter(company=company).select_related("employee", "expense_account", "category")
    if request.GET.get("visa") != "alla":
        claims = claims.filter(is_paid=False)
    return JsonResponse([_expense_json(c) for c in claims[:LIST_LIMIT]], safe=False)


@api_view()
def expense_detail(request, company, pk):
    return JsonResponse(_expense_json(_expense(company, pk), detail=True))


@api_view(("POST",))
def mileage_reports(request, company):
    """Körrapport, as payroll:mileage_report_create: submit() creates the expense claim
    (7331 against 2820) as a draft. Answers with that claim."""
    data = json_body(request)
    form = MileageReportForm(form_data(data), company=company)
    if not form.is_valid():
        raise form_error(form)
    report = form.save(commit=False)
    report.company = company
    claim = report.submit(request.user, register=False)
    return JsonResponse(_expense_json(claim, detail=True), status=201)


# --- supplier invoices -----------------------------------------------------------------


def _supplier_invoice(company, pk):
    return get_object_or_404(SupplierInvoice.objects.select_related("supplier"), pk=pk, company=company)


@api_view()
def supplier_invoices(request, company):
    invoices = SupplierInvoice.objects.filter(company=company).select_related("supplier")
    if request.GET.get("visa") != "alla":
        invoices = invoices.filter(is_paid=False)
    return JsonResponse([_supplier_invoice_json(i) for i in invoices[:LIST_LIMIT]], safe=False)


@api_view()
def supplier_invoice_detail(request, company, pk):
    return JsonResponse(_supplier_invoice_json(_supplier_invoice(company, pk), detail=True))


# --- customer invoices -----------------------------------------------------------------


def _customer_invoice(company, pk):
    return get_object_or_404(
        Invoice.objects.select_related("customer").prefetch_related("lines"), pk=pk, company=company
    )


@api_view()
def customer_invoices(request, company):
    invoices = company.outgoing_invoices.select_related("customer").prefetch_related("lines")
    if request.GET.get("visa") != "alla":
        invoices = invoices.filter(is_paid=False)
    return JsonResponse([_customer_invoice_json(i) for i in invoices[:LIST_LIMIT]], safe=False)


@api_view()
def customer_invoice_detail(request, company, pk):
    return JsonResponse(_customer_invoice_json(_customer_invoice(company, pk), detail=True))


# --- reports ---------------------------------------------------------------------------


def _year_json(year):
    if year is None:
        return None
    return {"id": year.pk, "name": year.name, "start_date": _date(year.start_date), "end_date": _date(year.end_date)}


def _report_json(context, sections, result, **extra):
    """`sections` are (title, rows, total_label, total, always) in display order, mirroring
    the web templates: a section without rows is left out unless `always`; a title-less
    section without rows is a result line (Rörelseresultat). Rows carry the amount the web
    shows - `amount` on the income statement, `balance` on the balance sheet."""
    return {
        "years": [_year_json(year) for year in context["years"]],
        "selected_year": _year_json(context["selected_year"]),
        "sections": [
            {
                "title": title,
                "rows": [
                    {"account": _account_json(row["account"]), "amount": _amount(row.get("amount", row.get("balance")))}
                    for row in rows
                ],
                "total_label": total_label,
                "total": None if total is None else _amount(total),
            }
            for title, rows, total_label, total, always in sections
            if rows or always
        ],
        "result": result,
        **extra,
    }


@api_view()
def income_statement(request, company):
    """Resultaträkningen as the web shows it; same query parameters (`year`, `from_month`,
    `to_month`) as bookkeeping:income_statement."""
    c = build_income_statement_context(request, company)
    has_year_end = bool(c["year_end_rows"])
    sections = [
        (
            "Rörelsens intäkter",
            c["operating_income_rows"],
            "Summa rörelsens intäkter",
            c["total_operating_income"],
            True,
        ),
        (
            "Råvaror och förnödenheter",
            c["raw_material_rows"],
            "Summa råvaror och förnödenheter",
            c["total_raw_material"],
            False,
        ),
        (
            "Övriga externa kostnader",
            c["external_cost_rows"],
            "Summa övriga externa kostnader",
            c["total_external_costs"],
            False,
        ),
        ("Personalkostnader", c["personnel_rows"], "Summa personalkostnader", c["total_personnel_costs"], True),
        (None, [], "Rörelseresultat", c["operating_result"], True),
        (
            "Finansiella poster",
            c["financial_rows"],
            "Resultat efter finansiella poster",
            c["result_after_financial"],
            True,
        ),
        (
            "Resultat och skatt",
            c["year_end_rows"],
            "Resultat efter skatt" if has_year_end else None,
            c["result_after_taxes"] if has_year_end else None,
            False,
        ),
        (None, c["results_and_tax_rows"], None, None, False),
    ]
    result = {
        "label": "Årets resultat",
        "amount": _amount(c["annual_result"]),
        "note": "Året är avslutat – årets resultat är överfört till eget kapital via bokslutsverifikationen, "
        "därför visar resultaträkningen 0."
        if c["year_is_closed"]
        else None,
    }
    return JsonResponse(
        _report_json(
            c,
            sections,
            result,
            month_choices=[{"value": m["value"], "label": m["label"]} for m in c["month_choices"]],
            from_month=c["from_month"],
            to_month=c["to_month"],
            period_start=_date(c["period_start"]),
            period_end=_date(c["period_end"]),
        )
    )


@api_view()
def balance_sheet(request, company):
    """Balansräkningen as the web shows it (`?year=` selects the year; balances are
    cumulative up to its last day). The difference between the two sides is the result
    not yet transferred to equity."""
    c = build_balance_sheet_context(request, company)
    sections = [
        ("Tillgångar", c["assets"], "Summa tillgångar", c["total_assets"], True),
        ("Eget kapital", c["equity"], "Summa eget kapital", c["total_equity"], False),
        (
            "Obeskattade reserver",
            c["untaxed_reserves"],
            "Summa obeskattade reserver",
            c["total_untaxed_reserves"],
            False,
        ),
        ("Avsättningar", c["provisions"], "Summa avsättningar", c["total_provisions"], False),
        (
            "Långfristiga skulder",
            c["long_term_liabilities"],
            "Summa långfristiga skulder",
            c["total_long_term_liabilities"],
            False,
        ),
        (
            "Kortfristiga skulder",
            c["current_liabilities"],
            "Summa kortfristiga skulder",
            c["total_current_liabilities"],
            False,
        ),
        (None, [], "Summa eget kapital och skulder", c["total_equity_and_liabilities"], True),
    ]
    result = {"label": "Beräknat resultat", "amount": _amount(c["balance_difference"]), "note": None}
    return JsonResponse(_report_json(c, sections, result))
