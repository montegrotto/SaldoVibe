from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from fixed_assets.models import get_due_alert_state_for_company
from invoicing.models import get_due_alert_state_for_company as get_recurring_invoice_due_alert_state
from payroll.models import PayrollRun
from supplier_invoices.models import SupplierInvoice

from .company_scope import get_active_company, get_user_companies, is_read_only_member
from .models import ExportJob
from .notifications import (
    get_failed_job_state,
    get_overdue_customer_invoice_state,
    get_overdue_supplier_invoice_state,
    get_vat_deadline_state,
)

FIXED_ASSET_ALERT_ACK_SESSION_KEY = "fixed_assets_alert_ack"


def environment(request):
    return {"is_dev_environment": settings.DEBUG}


def get_topbar_alert_state_for_company(company):
    if company is None:
        return {
            "fixed_assets_due_count": 0,
            "supplier_invoices_due_soon_count": 0,
            "recurring_invoices_due_count": 0,
            "payroll_runs_due_soon_count": 0,
            "export_jobs_ready_count": 0,
            "overdue_customer_invoices_count": 0,
            "overdue_supplier_invoices_count": 0,
            "vat_deadlines_count": 0,
            "failed_email_jobs_count": 0,
            "topbar_alert_count": 0,
            "topbar_alert_signature": "",
        }

    _, fixed_assets_due_count, fixed_assets_signature = get_due_alert_state_for_company(company)

    today = timezone.localdate()
    due_soon_threshold = today + timedelta(days=3)
    due_soon_invoice_ids = list(
        SupplierInvoice.objects.filter(
            company=company,
            is_paid=False,
            due_date__gte=today,
            due_date__lte=due_soon_threshold,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    supplier_due_soon_count = len(due_soon_invoice_ids)
    supplier_signature = "|".join(str(invoice_id) for invoice_id in due_soon_invoice_ids)

    _, recurring_invoices_due_count, recurring_invoices_signature = get_recurring_invoice_due_alert_state(company)

    due_soon_payroll_run_ids = list(
        PayrollRun.objects.filter(
            company=company,
            payment_date__gte=today,
            payment_date__lte=due_soon_threshold,
        )
        .annotate(net_total=Coalesce(Sum("salary_records__net_salary"), Decimal("0.00")))
        .filter(paid_amount__lt=F("net_total"))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    payroll_due_soon_count = len(due_soon_payroll_run_ids)
    payroll_signature = "|".join(str(run_id) for run_id in due_soon_payroll_run_ids)

    # Unlike the other alerts above, this one isn't dismissed by clicking the bell (see the
    # signature/ack logic in active_company()) - it clears when the user visits the export page
    # (bookkeeping/views/export.py::export_bundle_form marks matching jobs' seen_at).
    export_jobs_ready_count = ExportJob.objects.filter(
        company=company, status=ExportJob.Status.COMPLETED, seen_at__isnull=True
    ).count()

    _, overdue_customer_count, overdue_customer_signature = get_overdue_customer_invoice_state(company, today)
    _, overdue_supplier_count, overdue_supplier_signature = get_overdue_supplier_invoice_state(company, today)
    _, vat_deadline_count, vat_deadline_signature = get_vat_deadline_state(company, today)
    _, failed_jobs_count, failed_jobs_signature = get_failed_job_state(company, today)

    topbar_alert_count = (
        fixed_assets_due_count
        + supplier_due_soon_count
        + recurring_invoices_due_count
        + payroll_due_soon_count
        + export_jobs_ready_count
        + overdue_customer_count
        + overdue_supplier_count
        + vat_deadline_count
        + failed_jobs_count
    )
    topbar_alert_signature = (
        f"assets:{fixed_assets_signature}#supplier:{supplier_signature}"
        f"#recurring:{recurring_invoices_signature}#payroll:{payroll_signature}"
        f"#overduecust:{overdue_customer_signature}#overduesupp:{overdue_supplier_signature}"
        f"#vat:{vat_deadline_signature}#failedjobs:{failed_jobs_signature}"
    )

    return {
        "fixed_assets_due_count": fixed_assets_due_count,
        "supplier_invoices_due_soon_count": supplier_due_soon_count,
        "recurring_invoices_due_count": recurring_invoices_due_count,
        "payroll_runs_due_soon_count": payroll_due_soon_count,
        "export_jobs_ready_count": export_jobs_ready_count,
        "overdue_customer_invoices_count": overdue_customer_count,
        "overdue_supplier_invoices_count": overdue_supplier_count,
        "vat_deadlines_count": vat_deadline_count,
        "failed_email_jobs_count": failed_jobs_count,
        "topbar_alert_count": topbar_alert_count,
        "topbar_alert_signature": topbar_alert_signature,
    }


def active_company(request):
    if not request.user.is_authenticated:
        return {
            "active_company": None,
            "active_company_readonly": False,
            "available_companies": [],
            "fixed_assets_due_count": 0,
            "supplier_invoices_due_soon_count": 0,
            "recurring_invoices_due_count": 0,
            "payroll_runs_due_soon_count": 0,
            "export_jobs_ready_count": 0,
            "overdue_customer_invoices_count": 0,
            "overdue_supplier_invoices_count": 0,
            "vat_deadlines_count": 0,
            "failed_email_jobs_count": 0,
            "topbar_alert_count": 0,
            "topbar_alert_has_any": False,
            "topbar_alert_has_alert": False,
            "fixed_assets_due_has_alert": False,
        }

    companies = get_user_companies(request.user)
    company = get_active_company(request)
    state = get_topbar_alert_state_for_company(company)

    due_count = state["fixed_assets_due_count"]
    supplier_due_soon_count = state["supplier_invoices_due_soon_count"]
    recurring_invoices_due_count = state["recurring_invoices_due_count"]
    payroll_due_soon_count = state["payroll_runs_due_soon_count"]
    export_jobs_ready_count = state["export_jobs_ready_count"]
    topbar_alert_count = state["topbar_alert_count"]
    has_any_due = due_count > 0
    topbar_alert_has_any = topbar_alert_count > 0

    ack_map = request.session.get(FIXED_ASSET_ALERT_ACK_SESSION_KEY, {})
    acknowledged_signature = ack_map.get(str(company.pk), "") if company is not None else ""
    has_highlight = has_any_due and state["topbar_alert_signature"] != acknowledged_signature
    # export_jobs_ready_count is dismissed by visiting the export page (its seen_at, not this
    # session ack), so it always contributes to the alert styling independently of the signature.
    topbar_alert_has_alert = (
        topbar_alert_has_any and state["topbar_alert_signature"] != acknowledged_signature
    ) or export_jobs_ready_count > 0

    return {
        "active_company": company,
        "active_company_readonly": company is not None and is_read_only_member(request.user, company),
        "available_companies": companies,
        "fixed_assets_due_count": due_count,
        "supplier_invoices_due_soon_count": supplier_due_soon_count,
        "recurring_invoices_due_count": recurring_invoices_due_count,
        "payroll_runs_due_soon_count": payroll_due_soon_count,
        "export_jobs_ready_count": export_jobs_ready_count,
        "overdue_customer_invoices_count": state["overdue_customer_invoices_count"],
        "overdue_supplier_invoices_count": state["overdue_supplier_invoices_count"],
        "vat_deadlines_count": state["vat_deadlines_count"],
        "failed_email_jobs_count": state["failed_email_jobs_count"],
        "topbar_alert_count": topbar_alert_count,
        "topbar_alert_has_any": topbar_alert_has_any,
        "topbar_alert_has_alert": topbar_alert_has_alert,
        "fixed_assets_due_has_any": has_any_due,
        "fixed_assets_due_has_alert": has_highlight,
    }
