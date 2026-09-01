"""Start page, liquidity forecast and the compliance overview."""

import logging
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from attachments.models import TransactionAttachment
from attachments.utils import exclude_used_attachments
from auditlog.models import AuditChainAnchor, AuditLogEntry
from auditlog.services import calculate_audit_entry_hash
from invoicing.models import Invoice
from payroll.models import PayrollRun
from supplier_invoices.models import SupplierInvoice
from vat.models import VatCloseSnapshot
from vat.services import build_vat_source_fingerprint

from ..compliance_policy import require_compliance_action
from ..context_processors import active_company
from ..forms import (
    LiquidityForecastAccountFormSet,
)
from ..models import (
    Account,
    AccountingYear,
    JournalEntry,
    Transaction,
)
from ..pdf import company_logo_size, render_pdf_response
from ..reports import (
    build_system_documentation_context,
    get_year_context,
)
from ._base import company_required

logger = logging.getLogger(__name__)


def _liquidity_accounts_with_balances(company):
    """Bank/cash accounts (class 19) for the company, annotated with their current balance."""
    return (
        Account.objects.filter(company=company, number__startswith="19", is_active=True)
        .annotate(
            liquidity_debit=Sum("journalentry__debit"),
            liquidity_credit=Sum("journalentry__credit"),
        )
        .order_by("number")
    )


def _is_included_in_liquidity_forecast(account, balance):
    """Explicit user choice wins; otherwise default to non-zero bank accounts (193x)."""
    if account.include_in_liquidity_forecast is not None:
        return account.include_in_liquidity_forecast
    return account.number.startswith("193") and balance != Decimal("0")


@login_required
@company_required
def dashboard(request, company):

    today = timezone.localdate()
    current_year = (
        AccountingYear.objects.filter(company=company, start_date__lte=today, end_date__gte=today)
        .order_by("-start_date", "-id")
        .first()
    )

    # Quick P&L summary for current accounting year only.
    pnl_entries = (
        JournalEntry.objects.filter(
            account__company=company,
            transaction__accounting_year=current_year,
        )
        if current_year
        else JournalEntry.objects.none()
    )

    revenue = pnl_entries.filter(account__account_class="3").aggregate(c=Sum("credit"), d=Sum("debit"))
    rev_total = (revenue["c"] or Decimal("0")) - (revenue["d"] or Decimal("0"))

    costs = pnl_entries.filter(
        account__account_class__in=["4", "5", "6", "7", "8"],
    ).aggregate(c=Sum("credit"), d=Sum("debit"))
    cost_total = (costs["d"] or Decimal("0")) - (costs["c"] or Decimal("0"))
    net_result = rev_total - cost_total

    # Cash / bank balance (account class 1), limited to accounts selected for the liquidity forecast.
    liquidity_accounts = list(_liquidity_accounts_with_balances(company))
    account_balances = []
    excluded_liquidity_account_count = 0
    for account in liquidity_accounts:
        balance = (account.liquidity_debit or Decimal("0")) - (account.liquidity_credit or Decimal("0"))
        if _is_included_in_liquidity_forecast(account, balance):
            account_balances.append({"account": account, "balance": balance})
        else:
            excluded_liquidity_account_count += 1
    cash_balance = sum((row["balance"] for row in account_balances), Decimal("0"))

    # Liquidity forecast: weekly closing balance from current week and 10 weeks ahead.
    current_week_start = today - timedelta(days=today.weekday())
    projection_end_date = current_week_start + timedelta(weeks=10, days=6)

    incoming_invoice_events = {}
    incoming_invoices = Invoice.objects.filter(
        company=company, due_date__gte=today, due_date__lte=projection_end_date
    ).prefetch_related("lines")
    for invoice in incoming_invoices:
        due_date = invoice.due_date
        incoming_invoice_events[due_date] = incoming_invoice_events.get(due_date, Decimal("0.00")) + (
            invoice.total_amount or Decimal("0.00")
        )

    supplier_payment_events = {}
    supplier_due = (
        SupplierInvoice.objects.filter(
            company=company,
            is_paid=False,
            due_date__gte=today,
            due_date__lte=projection_end_date,
        )
        .values("due_date")
        .annotate(total=Sum("total_amount"))
    )
    for row in supplier_due:
        supplier_payment_events[row["due_date"]] = row["total"] or Decimal("0.00")

    salary_payment_events = {}
    payroll_runs = (
        PayrollRun.objects.filter(company=company, payment_date__gte=today, payment_date__lte=projection_end_date)
        .prefetch_related("salary_records")
        .order_by("payment_date", "id")
    )
    for payroll_run in payroll_runs:
        payout_date = payroll_run.payment_date
        payout_amount = sum(
            (record.net_salary or Decimal("0.00") for record in payroll_run.salary_records.all()), Decimal("0.00")
        )
        salary_payment_events[payout_date] = salary_payment_events.get(payout_date, Decimal("0.00")) + payout_amount

    labels = []
    projected_balance_series = []
    incoming_series = []
    supplier_series = []
    salary_series = []
    running_balance = cash_balance

    for week_offset in range(10):
        week_start = current_week_start + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        effective_start = max(today, week_start)

        incoming_amount = sum(
            (
                amount
                for event_date, amount in incoming_invoice_events.items()
                if effective_start <= event_date <= week_end
            ),
            Decimal("0.00"),
        )
        supplier_amount = sum(
            (
                amount
                for event_date, amount in supplier_payment_events.items()
                if effective_start <= event_date <= week_end
            ),
            Decimal("0.00"),
        )
        salary_amount = sum(
            (
                amount
                for event_date, amount in salary_payment_events.items()
                if effective_start <= event_date <= week_end
            ),
            Decimal("0.00"),
        )

        iso_calendar = week_start.isocalendar()
        labels.append(f"v.{iso_calendar.week}")
        if week_offset == 0:
            # Första punkten visar nuläge; resterande punkter visar startsaldo för veckan.
            projected_balance_series.append(float(cash_balance))
        else:
            projected_balance_series.append(float(running_balance))
        incoming_series.append(float(incoming_amount))
        supplier_series.append(float(supplier_amount))
        salary_series.append(float(salary_amount))

        running_balance += incoming_amount
        running_balance -= supplier_amount
        running_balance -= salary_amount

    incoming_total = sum(incoming_invoice_events.values(), Decimal("0.00"))
    supplier_total = sum(supplier_payment_events.values(), Decimal("0.00"))
    salary_total = sum(salary_payment_events.values(), Decimal("0.00"))
    projected_end_balance = running_balance

    # Invoice status chart: 5 weeks back, current week, and 3 weeks forward.
    invoice_window_start = current_week_start - timedelta(weeks=5)
    invoice_week_labels = []
    invoice_week_starts = []
    for week_offset in range(9):
        week_start = invoice_window_start + timedelta(weeks=week_offset)
        invoice_week_starts.append(week_start)
        invoice_week_labels.append(f"v.{week_start.isocalendar().week}")

    week_index_by_start = {week_start: idx for idx, week_start in enumerate(invoice_week_starts)}

    customer_paid_series = [Decimal("0.00") for _ in range(9)]
    customer_unpaid_series = [Decimal("0.00") for _ in range(9)]
    supplier_paid_series = [Decimal("0.00") for _ in range(9)]
    supplier_unpaid_series = [Decimal("0.00") for _ in range(9)]

    invoice_window_end = invoice_window_start + timedelta(weeks=9, days=6)

    # A paid invoice belongs to the week it was paid, an unpaid one to the week it falls
    # due - same split as the supplier invoices below.
    customer_invoices = (
        Invoice.objects.filter(company=company)
        .filter(
            Q(is_paid=False, due_date__gte=invoice_window_start, due_date__lte=invoice_window_end)
            | Q(is_paid=True, payment_date__gte=invoice_window_start, payment_date__lte=invoice_window_end)
        )
        .prefetch_related("lines")
    )
    for invoice in customer_invoices:
        event_date = invoice.payment_date if invoice.is_paid and invoice.payment_date else invoice.due_date
        week_start = event_date - timedelta(days=event_date.weekday())
        week_index = week_index_by_start.get(week_start)
        if week_index is None:
            continue

        if invoice.is_paid:
            customer_paid_series[week_index] += invoice.total_amount or Decimal("0.00")
        else:
            customer_unpaid_series[week_index] += invoice.total_amount or Decimal("0.00")

    supplier_invoices = SupplierInvoice.objects.filter(company=company).filter(
        Q(is_paid=False, due_date__gte=invoice_window_start, due_date__lte=invoice_window_end)
        | Q(is_paid=True, payment_date__gte=invoice_window_start, payment_date__lte=invoice_window_end)
    )
    for invoice in supplier_invoices:
        event_date = invoice.payment_date if invoice.is_paid and invoice.payment_date else invoice.due_date
        week_start = event_date - timedelta(days=event_date.weekday())
        week_index = week_index_by_start.get(week_start)
        if week_index is None:
            continue

        if invoice.is_paid:
            supplier_paid_series[week_index] += invoice.total_amount or Decimal("0.00")
        else:
            supplier_unpaid_series[week_index] += invoice.total_amount or Decimal("0.00")

    context = {
        "rev_total": rev_total,
        "cost_total": cost_total,
        "net_result": net_result,
        "cash_balance": cash_balance,
        "account_balances": account_balances,
        "excluded_liquidity_account_count": excluded_liquidity_account_count,
        "incoming_total": incoming_total,
        "supplier_total": supplier_total,
        "salary_total": salary_total,
        "projected_end_balance": projected_end_balance,
        "liquidity_chart": {
            "labels": labels,
            "projected_balance": projected_balance_series,
            "incoming_invoices": incoming_series,
            "supplier_payments": supplier_series,
            "salary_payments": salary_series,
        },
        "invoice_status_chart": {
            "labels": invoice_week_labels,
            "current_week_index": 5,
            "customer_paid": [float(value) for value in customer_paid_series],
            "customer_unpaid": [float(value) for value in customer_unpaid_series],
            "supplier_paid": [float(value) for value in supplier_paid_series],
            "supplier_unpaid": [float(value) for value in supplier_unpaid_series],
        },
    }
    return render(request, "bookkeeping/dashboard.html", context)


@login_required
@company_required
def liquidity_forecast_accounts(request, company):

    queryset = _liquidity_accounts_with_balances(company)

    if request.method == "POST":
        formset = LiquidityForecastAccountFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            formset.save()
            messages.success(request, "Kontovalet för likviditetsprognosen har uppdaterats.")
            return redirect("bookkeeping:dashboard")
    else:
        formset = LiquidityForecastAccountFormSet(queryset=queryset)

    for form in formset.forms:
        form.display_balance = (form.instance.liquidity_debit or Decimal("0")) - (
            form.instance.liquidity_credit or Decimal("0")
        )

    return render(
        request,
        "bookkeeping/liquidity_forecast_accounts.html",
        {"formset": formset},
    )


@login_required
@require_compliance_action("audit.verify_chain")
@company_required
def compliance_dashboard(request, company):

    years, selected_year = get_year_context(request, company)
    if selected_year is None:
        messages.error(request, "Skapa minst ett räkenskapsår för att se compliance-översikten.")
        return redirect("bookkeeping:accounting_year_create")

    transactions_qs = (
        Transaction.objects.filter(accounting_year=selected_year)
        .prefetch_related("entries__account")
        .order_by("voucher_series", "voucher_number", "date", "id")
    )

    voucher_gaps = []
    grouped_numbers = defaultdict(set)
    for txn in transactions_qs:
        if txn.voucher_number is None:
            continue
        grouped_numbers[txn.voucher_series or "A"].add(txn.voucher_number)

    for series, numbers in sorted(grouped_numbers.items()):
        if not numbers:
            continue
        max_number = max(numbers)
        missing = [n for n in range(1, max_number + 1) if n not in numbers]
        if missing:
            voucher_gaps.append(
                {
                    "series": series,
                    "missing_count": len(missing),
                    "sample": ", ".join(str(n) for n in missing[:10]),
                }
            )

    late_postings = []
    for txn in transactions_qs:
        lag_days = (txn.created_at.date() - txn.date).days
        if lag_days > 7:
            late_postings.append(
                {
                    "id": txn.id,
                    "reference": txn.reference or f"{txn.voucher_series}{txn.voucher_number or ''}",
                    "date": txn.date,
                    "created_at": txn.created_at,
                    "lag_days": lag_days,
                }
            )

    supplier_invoices_without_attachments = SupplierInvoice.objects.filter(
        company=company,
        is_registered=True,
        attachments__isnull=True,
    ).count()

    orphan_attachments = exclude_used_attachments(
        TransactionAttachment.objects.filter(company=company, deleted_at__isnull=True)
    ).count()

    lock_attempt_events_30d = (
        AuditLogEntry.objects.filter(
            company=company,
            occurred_at__gte=timezone.now() - timedelta(days=30),
        )
        .filter(Q(summary__icontains="låst") | Q(summary__icontains="period"))
        .count()
    )

    # This company's own hash chain (hash_version>=2, keyed by chain_key - see
    # auditlog.services.create_audit_log). Legacy hash_version=1 entries chain globally
    # across companies and are covered by the scheduled verify_audit_chain run instead.
    chain_mismatch_count = 0
    prev_hash = ""
    audit_entries = AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=str(company.pk)).order_by("id")
    for entry in audit_entries:
        expected_hash = calculate_audit_entry_hash(entry, prev_hash)
        if entry.prev_hash != prev_hash or entry.entry_hash != expected_hash:
            chain_mismatch_count += 1
        prev_hash = entry.entry_hash or expected_hash

    # External timestamp anchors cover every chain's tip; shown here as context, not
    # recomputed/reverified on every dashboard load (that happens via the monthly
    # scheduled job; see auditlog.management.commands.verify_audit_chain_anchors).
    last_chain_anchor = AuditChainAnchor.objects.order_by("-id").first()

    # R-008: a VatCloseSnapshot freezes the fingerprint of the period's VAT rows at
    # close time; recomputing it now reveals any post-close drift in the underlying data.
    vat_snapshot_drift = []
    for snapshot in VatCloseSnapshot.objects.filter(company=company).order_by("period_start"):
        current_fingerprint, _ = build_vat_source_fingerprint(
            company=company,
            start_date=snapshot.period_start,
            end_date=snapshot.period_end,
        )
        if current_fingerprint != snapshot.source_fingerprint:
            vat_snapshot_drift.append(snapshot)

    context = {
        "years": years,
        "selected_year": selected_year,
        "voucher_gaps": voucher_gaps,
        "late_postings": late_postings[:50],
        "late_postings_count": len(late_postings),
        "supplier_invoices_without_attachments": supplier_invoices_without_attachments,
        "orphan_attachments": orphan_attachments,
        "lock_attempt_events_30d": lock_attempt_events_30d,
        "chain_mismatch_count": chain_mismatch_count,
        "last_chain_anchor": last_chain_anchor,
        "vat_snapshot_drift": vat_snapshot_drift,
    }
    return render(request, "bookkeeping/compliance_dashboard.html", context)


@login_required
@company_required
def system_documentation(request, company):

    context = build_system_documentation_context(company)
    return render(request, "bookkeeping/system_documentation.html", context)


@login_required
@company_required
def system_documentation_pdf(request, company):

    context = build_system_documentation_context(company)
    context["logo_size"] = company_logo_size(company)
    org_digits = re.sub(r"\D", "", company.org_number or "") or f"company{company.pk}"
    filename = f"systemdokumentation_{org_digits}.pdf"
    return render_pdf_response("bookkeeping/system_documentation_pdf.html", context, filename)


@login_required
def topbar_alert_status(request):
    """Polled from `base.html` so the topbar bell (fixed assets, invoices due, payroll,
    finished export packages, ...) updates without a full page reload — otherwise a user
    sitting on one page while a background export finishes never sees the notification, since
    `active_company`/`get_topbar_alert_state_for_company` only run on a fresh page render.

    Reuses the `active_company` context processor directly (it's a plain function of `request`)
    so this can never drift from what a normal page load would show.
    """
    context = active_company(request)
    html = render_to_string("bookkeeping/_topbar_alert_bell.html", context)
    return JsonResponse(
        {
            "has_any": context["topbar_alert_has_any"],
            "token": f"{context['topbar_alert_count']}:{context['topbar_alert_has_alert']}",
            "html": html,
        }
    )
