from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attachments.services import first_extraction_suggestion
from attachments.utils import is_safe_return_to, replace_query_param
from attachments.view_helpers import (
    add_attachments,
    attachment_panel_context,
    picker_selection,
    remove_attachment,
)
from bookkeeping.balances import build_account_balances
from bookkeeping.company_scope import require_active_company, require_company
from bookkeeping.view_utils import (
    offset_payable_view,
    payable_payment_context,
    register_payable_payment_view,
    run_document_action,
    unmark_payable_manually_paid_view,
)

from .forms import (
    SupplierForm,
    SupplierInvoiceForm,
    build_cost_line_formset,
)
from .models import Supplier, SupplierInvoice, SupplierInvoiceCostLine


def _get_default_invoice_accounts(company):
    payable_account = company.accounts.filter(is_active=True, number="2440").first()
    vat_account = company.accounts.filter(is_active=True, number="2640").first()
    return payable_account, vat_account


@login_required
@require_company
def supplier_list(request, company):

    suppliers = company.suppliers.order_by("name")
    return render(request, "supplier_invoices/supplier_list.html", {"suppliers": suppliers})


@login_required
@require_company
def supplier_create(request, company):

    incoming_return_to = request.GET.get("return_to") if request.method == "GET" else request.POST.get("return_to")
    return_to = incoming_return_to if is_safe_return_to(incoming_return_to) else None

    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.company = company
            supplier.save()
            messages.success(request, "Leverantören har skapats.")
            if return_to:
                return redirect(replace_query_param(return_to, "supplier", supplier.pk))
            return redirect("supplier_invoices:supplier_list")
    else:
        # Namnet kan komma förifyllt från ReInvGrabbers leverantörsförslag på
        # fakturaformuläret, när ingen befintlig leverantör matchade namnet.
        form = SupplierForm(initial={"is_active": True, "name": request.GET.get("name", "")})

    return render(
        request,
        "supplier_invoices/supplier_form.html",
        {
            "form": form,
            "page_title_text": "Ny leverantör",
            "submit_label": "Skapa leverantör",
            "return_to": return_to,
            "cancel_url": return_to or reverse("supplier_invoices:supplier_list"),
        },
    )


@login_required
@require_company
def supplier_update(request, company, pk):

    supplier = get_object_or_404(Supplier, pk=pk, company=company)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "Leverantören har uppdaterats.")
            return redirect("supplier_invoices:supplier_list")
    else:
        form = SupplierForm(instance=supplier)

    return render(
        request,
        "supplier_invoices/supplier_form.html",
        {
            "form": form,
            "page_title_text": "Redigera leverantör",
            "submit_label": "Spara ändringar",
            "supplier": supplier,
            "cancel_url": reverse("supplier_invoices:supplier_list"),
        },
    )


@login_required
@require_company
def invoice_list(request, company):

    invoices = (
        SupplierInvoice.objects.filter(company=company)
        .select_related(
            "accounting_year",
            "supplier",
            "expense_account",
            "vat_account",
            "payable_account",
            "payment_account",
            "registered_transaction",
            "payment_transaction",
        )
        .prefetch_related(
            "attachments",
            "payments__transaction",
        )
    )

    return render(
        request,
        "supplier_invoices/invoice_list.html",
        {
            "invoices": invoices,
            "today_date": timezone.localdate(),
        },
    )


@login_required
@require_company
def supplier_last_invoice(request, company):
    """Kostnadsrader och belopp från leverantörens senast registrerade faktura.

    Backar upp fältet på fakturaformuläret som fyller i föregående fakturas
    kontering när man väljer leverantör, så att återkommande fakturor (hyra,
    telefoni, …) inte behöver konteras om från grunden varje gång.
    """
    supplier_id = request.GET.get("supplier", "")
    if not supplier_id.isdigit():
        return JsonResponse({"found": False})

    supplier = get_object_or_404(Supplier, pk=supplier_id, company=company)
    last_invoice = (
        SupplierInvoice.objects.filter(company=company, supplier=supplier)
        .prefetch_related("cost_lines__expense_account")
        .first()
    )
    if last_invoice is None:
        return JsonResponse({"found": False})

    return JsonResponse(
        {
            "found": True,
            "total_amount": str(last_invoice.total_amount),
            "vat_amount": str(last_invoice.vat_amount),
            "cost_lines": [
                {
                    "expense_account_id": line.expense_account_id,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                }
                for line in last_invoice.cost_lines.all()
            ],
        }
    )


@login_required
@require_company
def invoice_create(request, company):

    payable_account, vat_account = _get_default_invoice_accounts(company)
    selected_supplier_id = request.GET.get("supplier")

    selected_attachment_ids, selected_attachments = picker_selection(request, company)

    if request.method == "POST":
        form = SupplierInvoiceForm(request.POST, company=company)
        cost_line_formset = build_cost_line_formset(company=company, data=request.POST)
        if form.is_valid() and cost_line_formset.is_valid():
            cost_rows = []
            for row_form in cost_line_formset:
                cleaned = getattr(row_form, "cleaned_data", None) or {}
                if not cleaned or cleaned.get("DELETE"):
                    continue
                expense_account = cleaned.get("expense_account")
                debit = cleaned.get("debit") or Decimal("0.00")
                credit = cleaned.get("credit") or Decimal("0.00")
                if expense_account and (debit or credit):
                    cost_rows.append({"expense_account": expense_account, "debit": debit, "credit": credit})

            if not cost_rows:
                form.add_error(None, "Lägg till minst en kostnadsrad.")
            else:
                line_total = sum((row["debit"] - row["credit"] for row in cost_rows), Decimal("0.00"))
                vat_amount = form.cleaned_data.get("vat_amount") or Decimal("0.00")
                total_amount = form.cleaned_data.get("total_amount") or Decimal("0.00")
                if line_total + vat_amount != total_amount:
                    form.add_error(
                        None,
                        "Summan av kostnadsrader och moms måste vara lika med totalbelopp.",
                    )

        if form.is_valid() and cost_line_formset.is_valid() and not form.non_field_errors():
            invoice = form.save(commit=False)
            invoice.company = company
            invoice.created_by = request.user
            payable_account, vat_account = _get_default_invoice_accounts(company)
            invoice.payable_account = payable_account
            if (invoice.vat_amount or 0) > 0:
                invoice.vat_account = vat_account
            else:
                invoice.vat_account = None
            if invoice.supplier_id:
                invoice.supplier_name = invoice.supplier.name
            invoice.amount_ex_vat = sum((row["debit"] - row["credit"] for row in cost_rows), Decimal("0.00"))
            invoice.save()

            for row in cost_rows:
                SupplierInvoiceCostLine.objects.create(
                    invoice=invoice,
                    expense_account=row["expense_account"],
                    debit=row["debit"],
                    credit=row["credit"],
                )

            if selected_attachments.exists():
                invoice.attachments.add(*selected_attachments)

            if "register" in request.POST:
                try:
                    invoice.register_and_bookkeep(request.user)
                except ValidationError as exc:
                    messages.error(request, exc.messages[0])
                    return redirect("supplier_invoices:invoice_create")
                messages.success(request, "Leverantörsfakturan har registrerats och bokförts.")
            else:
                messages.success(request, "Leverantörsfakturan har sparats som utkast.")

            return redirect("supplier_invoices:invoice_list")
    else:
        extraction_suggestion = first_extraction_suggestion(selected_attachments)
        extraction_unmatched_vendor_name = ""

        if extraction_suggestion:
            vendor_name = (extraction_suggestion.get("leverantör") or "").strip()
            if vendor_name and not selected_supplier_id:
                matched_supplier = company.suppliers.filter(is_active=True, name__iexact=vendor_name).first()
                if matched_supplier:
                    selected_supplier_id = str(matched_supplier.pk)
                else:
                    extraction_unmatched_vendor_name = vendor_name

        form_initial = {"supplier": selected_supplier_id}
        if extraction_suggestion:
            field_map = {
                "fakturanummer": "invoice_number",
                "ocr_referens": "ocr_code",
                "datum": "invoice_date",
                "förfallodatum": "due_date",
                "totalbelopp": "total_amount",
                "momsbelopp": "vat_amount",
            }
            for source_key, form_field in field_map.items():
                value = extraction_suggestion.get(source_key)
                if value:
                    form_initial[form_field] = value

        form = SupplierInvoiceForm(company=company, initial=form_initial)
        cost_line_formset = build_cost_line_formset(company=company)

    picker_return_to = request.get_full_path()

    return render(
        request,
        "supplier_invoices/invoice_form.html",
        {
            "form": form,
            "cost_line_formset": cost_line_formset,
            "payable_account": payable_account,
            "vat_account": vat_account,
            "account_balances": build_account_balances(company),
            "prefill_supplier_id": selected_supplier_id if request.method == "GET" else "",
            "selected_attachments": selected_attachments,
            "selected_attachment_ids_csv": ",".join(str(attachment_id) for attachment_id in selected_attachment_ids),
            "picker_return_to": picker_return_to,
            "extraction_applied": bool(extraction_suggestion) if request.method == "GET" else False,
            "extraction_unmatched_vendor_name": (extraction_unmatched_vendor_name if request.method == "GET" else ""),
        },
    )


@login_required
@require_company
def invoice_detail(request, company, invoice_id):

    invoice = get_object_or_404(
        SupplierInvoice.objects.select_related(
            "supplier", "accounting_year", "registered_transaction", "payment_transaction"
        ).prefetch_related("cost_lines__expense_account", "attachments", "payments__transaction"),
        pk=invoice_id,
        company=company,
    )

    return_to = request.GET.get("return_to")
    back_url = return_to if is_safe_return_to(return_to) else reverse("supplier_invoices:invoice_list")

    return render(
        request,
        "supplier_invoices/invoice_detail.html",
        {
            "invoice": invoice,
            "back_url": back_url,
            **payable_payment_context(invoice),
            "today_date": timezone.localdate(),
            **attachment_panel_context(
                request,
                company=company,
                document=invoice,
                period_date=invoice.invoice_date,
                attach_url=reverse("supplier_invoices:invoice_attachment_add", args=[invoice.pk]),
                detach_url=reverse("supplier_invoices:invoice_attachment_remove", args=[invoice.pk]),
            ),
        },
    )


@login_required
@require_POST
@require_company
def invoice_attachment_add(request, company, invoice_id):
    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)
    return add_attachments(
        request,
        company=company,
        document=invoice,
        period_date=invoice.invoice_date,
        redirect_url=reverse("supplier_invoices:invoice_detail", args=[invoice.pk]),
    )


@login_required
@require_POST
@require_company
def invoice_attachment_remove(request, company, invoice_id):
    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)
    return remove_attachment(
        request,
        company=company,
        document=invoice,
        period_date=invoice.invoice_date,
        redirect_url=reverse("supplier_invoices:invoice_detail", args=[invoice.pk]),
        document_noun="fakturan",
    )


@login_required
@require_POST
@require_company
def invoice_register(request, company, invoice_id):

    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)

    if invoice.is_registered:
        messages.info(request, "Fakturan är redan bokförd.")
        return redirect("supplier_invoices:invoice_list")

    run_document_action(
        request,
        lambda: invoice.register_and_bookkeep(request.user),
        "Leverantörsfakturan har registrerats och bokförts.",
    )
    return redirect("supplier_invoices:invoice_list")


@login_required
@require_POST
@require_company
def invoice_register_payment(request, company, invoice_id):
    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)
    return register_payable_payment_view(request, invoice, fallback="supplier_invoices:invoice_list")


@login_required
@require_POST
@require_company
def invoice_offset(request, company, invoice_id):
    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)
    return offset_payable_view(request, invoice, fallback="supplier_invoices:invoice_list")


@login_required
@require_POST
@require_company
def invoice_unmark_manually_paid(request, company, invoice_id):
    invoice = get_object_or_404(SupplierInvoice, pk=invoice_id, company=company)
    return unmark_payable_manually_paid_view(request, invoice, fallback="supplier_invoices:invoice_list")


@login_required
def invoice_qr_svg(request, invoice_id):
    company = require_active_company(request)
    if company is None:
        return HttpResponse(status=403)

    invoice = get_object_or_404(SupplierInvoice.objects.select_related("supplier"), pk=invoice_id, company=company)
    svg = invoice.build_payment_qr_svg()
    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    return response
