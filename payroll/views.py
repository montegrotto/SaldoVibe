import io
import json
import zipfile
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from bookkeeping.company_scope import require_company
from bookkeeping.compliance_policy import require_compliance_action
from bookkeeping.models import SentEmail
from bookkeeping.outgoing_mail import company_email_configured, send_company_email
from bookkeeping.pdf import PdfRenderError, company_logo_size, render_pdf_bytes, render_pdf_response

from .forms import (
    EmployeeDefaultAdjustmentFormSet,
    EmployeeForm,
    PayrollRunCreateForm,
    SalaryAdjustmentFormSet,
    SalaryRecordAdjustmentForm,
)
from .models import (
    AdjustmentCategory,
    Employee,
    PayrollReportEvidence,
    PayrollRun,
    SalaryAdjustment,
    SalaryRecord,
    ensure_payroll_report_evidence,
    mark_payroll_run_finished,
    mark_payroll_run_reported,
    quantize_money,
)


def _adjustment_presets():
    return [
        {
            "label": "Skattepliktig förmån",
            "phase": SalaryAdjustment.Phase.PRE_TAX,
            "direction": SalaryAdjustment.Direction.ADDITION,
            "category": AdjustmentCategory.TAXABLE_BENEFIT,
            "description": "Skattepliktig förmån",
        },
        {
            "label": "OB-tillägg",
            "phase": SalaryAdjustment.Phase.PRE_TAX,
            "direction": SalaryAdjustment.Direction.ADDITION,
            "category": AdjustmentCategory.OB_SUPPLEMENT,
            "description": "OB-tillägg",
        },
        {
            "label": "Övertidsersättning",
            "phase": SalaryAdjustment.Phase.PRE_TAX,
            "direction": SalaryAdjustment.Direction.ADDITION,
            "category": AdjustmentCategory.OVERTIME,
            "description": "Övertidsersättning",
        },
        {
            "label": "Bonus",
            "phase": SalaryAdjustment.Phase.PRE_TAX,
            "direction": SalaryAdjustment.Direction.ADDITION,
            "category": AdjustmentCategory.BONUS,
            "description": "Bonus",
        },
        {
            "label": "Bruttolöneavdrag",
            "phase": SalaryAdjustment.Phase.PRE_TAX,
            "direction": SalaryAdjustment.Direction.DEDUCTION,
            "category": AdjustmentCategory.GROSS_SALARY_DEDUCTION,
            "description": "Bruttolöneavdrag",
        },
        {
            "label": "Skattefri milersättning",
            "phase": SalaryAdjustment.Phase.POST_TAX,
            "direction": SalaryAdjustment.Direction.ADDITION,
            "category": AdjustmentCategory.TAX_FREE_MILEAGE,
            "description": "Skattefri milersättning",
        },
        {
            "label": "Nettolöneavdrag",
            "phase": SalaryAdjustment.Phase.POST_TAX,
            "direction": SalaryAdjustment.Direction.DEDUCTION,
            "category": AdjustmentCategory.NET_SALARY_DEDUCTION,
            "description": "Nettolöneavdrag",
        },
    ]


def _copy_employee_default_adjustments(employee, salary_record):
    for default_adj in employee.default_adjustments.all():
        SalaryAdjustment.objects.create(
            salary_record=salary_record,
            phase=default_adj.phase,
            direction=default_adj.direction,
            category=default_adj.category,
            description=default_adj.description,
            amount=default_adj.amount,
            is_taxable=default_adj.is_taxable,
        )
    salary_record.save()


@login_required
@require_company
def employee_list(request, company):

    employees = company.employees.order_by("first_name", "last_name")
    return render(request, "payroll/employee_list.html", {"employees": employees})


@login_required
@require_company
def employee_create(request, company):

    if request.method == "POST":
        form = EmployeeForm(request.POST)
        formset = EmployeeDefaultAdjustmentFormSet(request.POST, prefix="defaults")
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                employee = form.save(commit=False)
                employee.company = company
                employee.save()
                formset.instance = employee
                formset.save()
            messages.success(request, "Anställd har skapats.")
            return redirect("payroll:employee_list")
    else:
        form = EmployeeForm(
            initial={"employment_rate": Decimal("100.00"), "tax_table_number": 32, "tax_table_column": 1}
        )
        formset = EmployeeDefaultAdjustmentFormSet(prefix="defaults")

    return render(
        request,
        "payroll/employee_form.html",
        {
            "form": form,
            "formset": formset,
            "adjustment_presets": _adjustment_presets(),
            "page_title_text": "Ny anställd",
            "submit_label": "Skapa anställd",
        },
    )


@login_required
@require_company
def employee_update(request, company, pk):

    employee = get_object_or_404(Employee, pk=pk, company=company)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee)
        formset = EmployeeDefaultAdjustmentFormSet(request.POST, instance=employee, prefix="defaults")
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Anställd har uppdaterats.")
            return redirect("payroll:employee_list")
    else:
        form = EmployeeForm(instance=employee)
        formset = EmployeeDefaultAdjustmentFormSet(instance=employee, prefix="defaults")

    return render(
        request,
        "payroll/employee_form.html",
        {
            "form": form,
            "formset": formset,
            "adjustment_presets": _adjustment_presets(),
            "page_title_text": "Redigera anställd",
            "submit_label": "Spara ändringar",
            "employee": employee,
        },
    )


@login_required
@require_company
def payroll_run_list(request, company):

    payroll_runs = company.payroll_runs.prefetch_related("salary_records__employee").order_by(
        "-period_year", "-period_month", "-created_at"
    )
    return render(request, "payroll/payroll_run_list.html", {"payroll_runs": payroll_runs})


@login_required
@require_company
def payroll_run_create(request, company):

    if request.method == "POST":
        form = PayrollRunCreateForm(request.POST)
        if form.is_valid():
            period_year = form.instance.period_year
            period_month = form.instance.period_month

            if PayrollRun.objects.filter(company=company, period_year=period_year, period_month=period_month).exists():
                form.add_error(None, "Det finns redan en lönekörning för vald period.")
            else:
                try:
                    with db_transaction.atomic():
                        payroll_run = form.save(commit=False)
                        payroll_run.company = company
                        payroll_run.created_by = request.user
                        payroll_run.save()

                        if form.cleaned_data.get("generate_salary_records"):
                            active_employees = company.employees.filter(is_active=True).order_by(
                                "first_name", "last_name"
                            )
                            for employee in active_employees:
                                gross_salary = quantize_money(
                                    (employee.monthly_salary or Decimal("0.00"))
                                    * (employee.employment_rate or Decimal("0.00"))
                                    / Decimal("100")
                                )
                                record = SalaryRecord.objects.create(
                                    payroll_run=payroll_run,
                                    employee=employee,
                                    gross_salary=gross_salary,
                                    tax_table_number=employee.tax_table_number,
                                    tax_table_column=employee.tax_table_column,
                                )
                                _copy_employee_default_adjustments(employee, record)
                except ValidationError as exc:
                    form.add_error(None, str(exc))
                else:
                    messages.success(request, "Lönekörningen har skapats.")
                    return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)
    else:
        form = PayrollRunCreateForm()

    return render(request, "payroll/payroll_run_form.html", {"form": form})


@login_required
@require_company
def payroll_run_detail(request, company, payroll_run_id):

    payroll_run = get_object_or_404(
        PayrollRun.objects.filter(company=company).prefetch_related(
            "salary_records__employee", "salary_records__adjustments"
        ),
        pk=payroll_run_id,
    )
    salary_records = payroll_run.salary_records.all()
    payment_reminders = payroll_run.payment_reminders.select_related("employee").all()
    existing_employee_ids = salary_records.values_list("employee_id", flat=True)
    available_employees = (
        company.employees.filter(is_active=True)
        .exclude(pk__in=existing_employee_ids)
        .order_by("first_name", "last_name")
    )
    totals = salary_records.aggregate(
        total_gross=Sum("gross_salary"),
        total_tax=Sum("preliminary_tax_amount"),
        total_employer=Sum("employer_contribution_amount"),
        total_net=Sum("net_salary"),
    )
    return render(
        request,
        "payroll/payroll_run_detail.html",
        {
            "payroll_run": payroll_run,
            "salary_records": salary_records,
            "payment_reminders": payment_reminders,
            "available_employees": available_employees,
            "totals": totals,
        },
    )


@login_required
@require_POST
@require_company
def payroll_run_add_employee(request, company, payroll_run_id):

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if payroll_run.is_finished or payroll_run.is_reported_to_skatteverket:
        messages.error(request, "Lönekörningen är avslutad och kan inte ändras.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    employee_id = request.POST.get("employee_id")
    employee = get_object_or_404(Employee, pk=employee_id, company=company)

    if SalaryRecord.objects.filter(payroll_run=payroll_run, employee=employee).exists():
        messages.warning(request, "Anställd finns redan i lönekörningen.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    gross_salary = quantize_money(
        (employee.monthly_salary or Decimal("0.00")) * (employee.employment_rate or Decimal("0.00")) / Decimal("100")
    )
    try:
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=employee,
            gross_salary=gross_salary,
            tax_table_number=employee.tax_table_number,
            tax_table_column=employee.tax_table_column,
        )
        _copy_employee_default_adjustments(employee, record)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)
    messages.success(request, f"{employee} har lagts till i lönekörningen.")
    return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)


@login_required
@require_POST
@require_company
def payroll_run_remove_employee(request, company, payroll_run_id, salary_record_id):

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if payroll_run.is_finished or payroll_run.is_reported_to_skatteverket:
        messages.error(request, "Lönekörningen är avslutad och kan inte ändras.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    record = get_object_or_404(SalaryRecord, pk=salary_record_id, payroll_run=payroll_run)
    employee_name = str(record.employee)
    record.delete()
    messages.success(request, f"{employee_name} har tagits bort från lönekörningen.")
    return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)


def salary_report_pdf_context(salary_record):
    """Kontext för lönespec-PDF:en — delas med exportpaketet (bookkeeping/export_bundle.py)."""
    employee = salary_record.employee
    company = salary_record.payroll_run.company
    employee_address_lines = [
        str(employee),
        employee.address,
        f"{employee.postal_code} {employee.city}".strip(),
    ]
    return {
        "salary_record": salary_record,
        "employee_address_lines": [line for line in employee_address_lines if line and line.strip()],
        "logo_size": company_logo_size(company),
    }


@login_required
@require_company
def salary_report_print(request, company, payroll_run_id, salary_record_id):

    salary_record = get_object_or_404(
        SalaryRecord.objects.select_related("payroll_run", "employee", "payroll_run__company")
        .prefetch_related("adjustments")
        .filter(
            payroll_run__company=company,
            payroll_run_id=payroll_run_id,
        ),
        pk=salary_record_id,
    )
    return render_pdf_response(
        "payroll/salary_report_print.html",
        salary_report_pdf_context(salary_record),
        f"lonespecifikation-{salary_record.pk}.pdf",
        disposition="inline",
    )


@login_required
@require_company
@require_POST
def salary_report_email(request, company, payroll_run_id, salary_record_id):
    """Skicka lönespecifikationen som PDF till den anställdes e-post via företagets utgående konto."""
    salary_record = get_object_or_404(
        SalaryRecord.objects.select_related("payroll_run", "employee", "payroll_run__company")
        .prefetch_related("adjustments")
        .filter(payroll_run__company=company, payroll_run_id=payroll_run_id),
        pk=salary_record_id,
    )
    employee = salary_record.employee
    run = salary_record.payroll_run
    if not employee.email:
        messages.error(request, f"{employee} saknar e-postadress.")
    elif not company_email_configured(company):
        messages.error(request, "Utgående e-post är inte konfigurerad för företaget.")
    else:
        try:
            pdf = render_pdf_bytes("payroll/salary_report_print.html", salary_report_pdf_context(salary_record))
        except PdfRenderError as exc:
            messages.error(request, str(exc))
            return redirect("payroll:payroll_run_detail", payroll_run_id=run.pk)
        period = f"{run.period_year}-{run.period_month:02d}"
        body = (
            f"Hej {employee.first_name}!\n\n"
            f"Här kommer din lönespecifikation för {period} från {company.name}, bifogad som PDF.\n\n"
            f"Vänliga hälsningar\n{company.name}"
        )
        result = send_company_email(
            company,
            purpose=SentEmail.Purpose.SALARY,
            to=[employee.email],
            subject=f"Lönespecifikation {period} från {company.name}",
            body=body,
            attachments=[(f"lonespecifikation-{period}.pdf", "application/pdf", pdf)],
            user=request.user,
        )
        if result.status == SentEmail.Status.SENT:
            messages.success(request, f"Lönespecifikationen skickades till {employee.email}.")
        else:
            messages.error(request, f"Kunde inte skicka lönespecifikationen: {result.error}")
    return redirect("payroll:payroll_run_detail", payroll_run_id=run.pk)


@login_required
@require_company
def salary_record_update(request, company, payroll_run_id, salary_record_id):

    salary_record = get_object_or_404(
        SalaryRecord.objects.select_related("payroll_run", "employee").filter(
            payroll_run__company=company,
            payroll_run_id=payroll_run_id,
        ),
        pk=salary_record_id,
    )

    if salary_record.payroll_run.is_finished or salary_record.payroll_run.is_reported_to_skatteverket:
        messages.error(request, "Lönekörningen är avslutad och kan inte ändras.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=salary_record.payroll_run.pk)

    if request.method == "POST":
        form = SalaryRecordAdjustmentForm(request.POST, instance=salary_record)
        formset = SalaryAdjustmentFormSet(request.POST, instance=salary_record)
        if form.is_valid() and formset.is_valid():
            try:
                salary_record = form.save()
                formset.save()
                salary_record.save()
            except ValidationError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Löneraden har uppdaterats.")
                return redirect("payroll:payroll_run_detail", payroll_run_id=salary_record.payroll_run.pk)
    else:
        form = SalaryRecordAdjustmentForm(instance=salary_record)
        formset = SalaryAdjustmentFormSet(instance=salary_record)

    return render(
        request,
        "payroll/salary_record_form.html",
        {
            "form": form,
            "formset": formset,
            "salary_record": salary_record,
            "payroll_run": salary_record.payroll_run,
            "adjustment_presets": _adjustment_presets(),
        },
    )


@login_required
@require_compliance_action("payroll.report_mark")
@require_company
def skatteverket_report_evidence_download(request, company, payroll_run_id):

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if not payroll_run.is_reported_to_skatteverket:
        messages.error(
            request, "AGI-bevispaket kan laddas ner först efter att lönekörningen markerats som rapporterad."
        )
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    evidence = PayrollReportEvidence.objects.filter(payroll_run=payroll_run).first()
    if evidence is None:
        evidence = ensure_payroll_report_evidence(payroll_run, user=request.user)

    manifest = {
        "schema": "agi-evidence-package-v1",
        "payroll_run_id": payroll_run.pk,
        "period": f"{payroll_run.period_year}-{payroll_run.period_month:02d}",
        "company": {
            "name": company.name,
            "org_number": company.org_number,
        },
        "payload_sha256": evidence.payload_hash,
        "generated_at": evidence.generated_at.isoformat(),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        zf.writestr("agi_payload.json", evidence.payload_json.encode("utf-8"))
    buf.seek(0)

    org = (company.org_number or "").replace("-", "").replace(" ", "") or f"company{company.pk}"
    filename = f"AGI_EVIDENCE_{org}_{payroll_run.period_year}{payroll_run.period_month:02d}.zip"
    response = HttpResponse(buf.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_compliance_action("payroll.report_mark")
@require_company
def skatteverket_agi_xml_download(request, company, payroll_run_id):
    from .agi import AGIValidationError, generate_agi_xml

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if not payroll_run.is_finished:
        messages.error(request, "Lönekörningen måste avslutas innan AGI-filen kan skapas.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    sender_name = request.user.get_full_name() or request.user.email
    sender_email = request.user.email or company.email
    sender_phone = company.phone_number

    try:
        xml_bytes = generate_agi_xml(
            payroll_run, sender_name=sender_name, sender_email=sender_email, sender_phone=sender_phone
        )
    except AGIValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    filename = f"AGI_{payroll_run.period_year}{payroll_run.period_month:02d}.xml"
    response = HttpResponse(xml_bytes, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
@require_compliance_action("payroll.report_mark")
@require_company
def skatteverket_report_mark_reported(request, company, payroll_run_id):

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if not payroll_run.is_finished:
        messages.error(request, "Lönekörningen måste avslutas innan den kan rapporteras till Skatteverket.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)
    mark_payroll_run_reported(payroll_run, user=request.user)
    messages.success(request, "Lönekörningen har markerats som rapporterad till Skatteverket.")
    return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)


@login_required
@require_POST
@require_company
def payroll_run_finish(request, company, payroll_run_id):

    payroll_run = get_object_or_404(PayrollRun, pk=payroll_run_id, company=company)
    if payroll_run.is_reported_to_skatteverket:
        messages.error(request, "Lönekörningen är redan rapporterad och kan inte avslutas på nytt.")
        return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)

    try:
        mark_payroll_run_finished(payroll_run, request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Lönekörningen har avslutats. Bokföring och lönepåminnelser har skapats.")
    return redirect("payroll:payroll_run_detail", payroll_run_id=payroll_run.pk)
