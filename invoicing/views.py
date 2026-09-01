from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attachments.utils import is_safe_return_to, replace_query_param
from attachments.view_helpers import (
    add_attachments,
    attachment_panel_context,
    picker_selection,
    remove_attachment,
)
from bookkeeping.company_scope import require_company
from bookkeeping.models import SentEmail
from bookkeeping.outgoing_mail import company_email_configured, send_company_email
from bookkeeping.pdf import PdfRenderError, company_logo_size, render_pdf_bytes, render_pdf_response
from bookkeeping.view_utils import (
    offset_payable_view,
    payable_payment_context,
    register_payable_payment_view,
    run_document_action,
    unmark_payable_manually_paid_view,
)

from .forms import (
    ArticleForm,
    CustomerForm,
    InvoiceForm,
    RecurringInvoiceForm,
    ReminderPrintForm,
    build_invoice_line_formset,
    build_recurring_invoice_line_formset,
)
from .models import Article, Customer, Invoice, InvoiceLine, InvoiceReminder, RecurringInvoice


def _build_article_context_maps(company):
    article_unit_price_map = {
        str(article.pk): format((article.unit_price or Decimal("0.00")).quantize(Decimal("0.01")), "f")
        for article in company.articles.filter(is_active=True).only("pk", "unit_price")
    }
    article_vat_rate_map = {
        str(article.pk): format((article.vat_rate or Decimal("0.00")).quantize(Decimal("0.01")), "f")
        for article in company.articles.filter(is_active=True).only("pk", "vat_rate")
    }
    article_meta_map = {
        str(article.pk): {
            "number": (article.article_number or "").strip(),
            "name": (article.name or "").strip(),
            "description": (article.description or "").strip(),
            "unit": (article.unit or "").strip(),
        }
        for article in company.articles.filter(is_active=True).only(
            "pk", "article_number", "name", "description", "unit"
        )
    }
    return article_unit_price_map, article_vat_rate_map, article_meta_map


def _save_line_formset(line_formset, *, parent, parent_field):
    created_item_lines = 0
    for line_form in line_formset:
        cleaned = getattr(line_form, "cleaned_data", None) or {}
        if not cleaned:
            continue
        if cleaned.get("DELETE"):
            if line_form.instance.pk:
                line_form.instance.delete()
            continue
        line_type = cleaned.get("line_type") or InvoiceLine.LINE_TYPE_ITEM
        description = (cleaned.get("description") or "").strip()
        if not description:
            continue
        if line_type == InvoiceLine.LINE_TYPE_ITEM and (
            cleaned.get("quantity") is None or cleaned.get("unit_price") is None
        ):
            continue
        line = line_form.save(commit=False)
        setattr(line, parent_field, parent)
        line.save()
        if line_type == InvoiceLine.LINE_TYPE_ITEM:
            created_item_lines += 1
    return created_item_lines


class _LineValidationAborted(Exception):
    """Internal control-flow signal used to roll back a line formset save when validation fails."""


@login_required
@require_company
def customer_list(request, company):

    customers = company.customers.order_by("name")
    return render(request, "invoicing/customer_list.html", {"customers": customers})


@login_required
@require_company
def customer_create(request, company):

    incoming_return_to = request.GET.get("return_to") if request.method == "GET" else request.POST.get("return_to")
    return_to = incoming_return_to if is_safe_return_to(incoming_return_to) else None

    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.company = company
            customer.save()
            messages.success(request, "Kunden har skapats.")
            if return_to:
                return redirect(replace_query_param(return_to, "customer", customer.pk))
            return redirect("invoicing:customer_list")
    else:
        form = CustomerForm(initial={"is_active": True})

    return render(
        request,
        "invoicing/customer_form.html",
        {
            "form": form,
            "page_title_text": "Ny kund",
            "submit_label": "Skapa kund",
            "return_to": return_to,
            "cancel_url": return_to or reverse("invoicing:customer_list"),
        },
    )


@login_required
@require_company
def customer_update(request, company, pk):

    customer = get_object_or_404(Customer, pk=pk, company=company)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, "Kunden har uppdaterats.")
            return redirect("invoicing:customer_list")
    else:
        form = CustomerForm(instance=customer)

    return render(
        request,
        "invoicing/customer_form.html",
        {
            "form": form,
            "page_title_text": "Redigera kund",
            "submit_label": "Spara ändringar",
            "customer": customer,
            "cancel_url": reverse("invoicing:customer_list"),
        },
    )


@login_required
@require_company
def article_list(request, company):

    articles = company.articles.order_by("article_number", "name")
    return render(request, "invoicing/article_list.html", {"articles": articles})


@login_required
@require_company
def article_create(request, company):

    if request.method == "POST":
        form = ArticleForm(request.POST, company=company)
        if form.is_valid():
            article = form.save(commit=False)
            article.company = company
            article.save()
            messages.success(request, "Artikeln har skapats.")
            return redirect("invoicing:article_list")
    else:
        form = ArticleForm(
            company=company,
            initial={"is_active": True, "vat_rate": Decimal("25.00"), "unit": "st"},
        )

    return render(
        request,
        "invoicing/article_form.html",
        {
            "form": form,
            "page_title_text": "Ny artikel",
            "submit_label": "Skapa artikel",
        },
    )


@login_required
@require_company
def article_update(request, company, pk):

    article = get_object_or_404(Article, pk=pk, company=company)
    if request.method == "POST":
        form = ArticleForm(request.POST, instance=article, company=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Artikeln har uppdaterats.")
            return redirect("invoicing:article_list")
    else:
        form = ArticleForm(instance=article, company=company)

    return render(
        request,
        "invoicing/article_form.html",
        {
            "form": form,
            "page_title_text": "Redigera artikel",
            "submit_label": "Spara ändringar",
            "article": article,
        },
    )


@login_required
@require_company
def invoice_list(request, company):

    invoices = (
        company.outgoing_invoices.select_related("customer")
        .prefetch_related("lines")
        .order_by("-invoice_date", "-created_at")
    )
    return render(request, "invoicing/invoice_list.html", {"invoices": invoices})


@login_required
@require_company
def invoice_create(request, company):

    requested_customer = None
    requested_customer_id = (request.GET.get("customer") or "").strip()
    customer_payment_terms_map = {
        str(customer.pk): customer.default_payment_terms_days
        for customer in company.customers.filter(is_active=True).only("pk", "default_payment_terms_days")
    }
    article_unit_price_map, article_vat_rate_map, article_meta_map = _build_article_context_maps(company)
    if requested_customer_id:
        try:
            requested_customer = company.customers.get(pk=int(requested_customer_id), is_active=True)
        except (TypeError, ValueError, Customer.DoesNotExist):
            requested_customer = None

    selected_attachment_ids, selected_attachments = picker_selection(request, company)

    if request.method == "POST":
        form = InvoiceForm(request.POST, company=company)
        line_formset = build_invoice_line_formset(company=company, data=request.POST)

        if form.is_valid() and line_formset.is_valid():
            invoice = form.save(commit=False)
            invoice.company = company
            invoice.currency = "SEK"
            invoice.payment_terms_days = invoice.customer.default_payment_terms_days or 30
            invoice.save()

            created_item_lines = _save_line_formset(line_formset, parent=invoice, parent_field="invoice")

            if created_item_lines == 0:
                invoice.delete()
                form.add_error(None, "Lägg till minst en artikelrad med beskrivning, antal och á-pris.")
            elif invoice.reverse_charge and invoice.vat_amount != Decimal("0.00"):
                invoice.delete()
                form.add_error(
                    None,
                    "Vid omvänd betalningsskyldighet får fakturan inte innehålla moms. Sätt momssatsen till 0% på raderna.",
                )
            else:
                if selected_attachments.exists():
                    invoice.attachments.add(*selected_attachments)

                if "book" in request.POST:
                    try:
                        invoice.bookkeep(request.user)
                    except ValidationError as exc:
                        messages.error(request, exc.messages[0])
                        messages.warning(request, f"Faktura {invoice.invoice_number} sparades som utkast.")
                    else:
                        messages.success(request, f"Faktura {invoice.invoice_number} skapad och bokförd.")
                else:
                    messages.success(request, f"Faktura {invoice.invoice_number} sparad som utkast.")
                return redirect("invoicing:invoice_list")
    else:
        today = timezone.localdate()
        default_payment_terms_days = 30
        if requested_customer is not None:
            default_payment_terms_days = requested_customer.default_payment_terms_days or 30
        initial_data = {
            "invoice_date": today,
            "due_date": today + timedelta(days=default_payment_terms_days),
        }
        if requested_customer is not None:
            initial_data["customer"] = requested_customer.pk
        form = InvoiceForm(
            company=company,
            initial=initial_data,
        )
        line_formset = build_invoice_line_formset(company=company)

    picker_return_to = request.get_full_path()

    return render(
        request,
        "invoicing/invoice_form.html",
        {
            "form": form,
            "line_formset": line_formset,
            "customer_payment_terms_map": customer_payment_terms_map,
            "article_unit_price_map": article_unit_price_map,
            "article_vat_rate_map": article_vat_rate_map,
            "article_meta_map": article_meta_map,
            "selected_attachments": selected_attachments,
            "selected_attachment_ids_csv": ",".join(str(attachment_id) for attachment_id in selected_attachment_ids),
            "picker_return_to": picker_return_to,
        },
    )


@login_required
@require_company
def invoice_detail(request, company, invoice_id):

    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "customer", "company", "booked_transaction", "payment_transaction", "recurring_invoice"
        ).prefetch_related(
            "lines",
            "booked_transaction__entries__account",
            "attachments",
            "payments__transaction",
            "reminders",
            "sent_emails",
        ),
        pk=invoice_id,
        company=company,
    )
    return_to = request.GET.get("return_to")
    back_url = return_to if is_safe_return_to(return_to) else reverse("invoicing:invoice_list")
    invoice_overdue = (
        invoice.is_booked
        and not invoice.is_paid
        and not invoice.is_credit_invoice
        and invoice.due_date is not None
        and invoice.due_date < timezone.localdate()
    )
    return render(
        request,
        "invoicing/invoice_detail.html",
        {
            "invoice": invoice,
            "back_url": back_url,
            **payable_payment_context(invoice),
            "invoice_date_is_today": invoice.invoice_date == timezone.localdate(),
            "invoice_overdue": invoice_overdue,
            "reminder_fee_default": invoice.company.reminder_fee,
            "reminder_pay_by_default": timezone.localdate() + timedelta(days=10),
            "email_configured": company_email_configured(company),
            "customer_has_email": bool(invoice.customer.email),
            **attachment_panel_context(
                request,
                company=company,
                document=invoice,
                period_date=invoice.invoice_date,
                attach_url=reverse("invoicing:invoice_attachment_add", args=[invoice.pk]),
                detach_url=reverse("invoicing:invoice_attachment_remove", args=[invoice.pk]),
            ),
        },
    )


@login_required
@require_POST
@require_company
def invoice_attachment_add(request, company, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)
    return add_attachments(
        request,
        company=company,
        document=invoice,
        period_date=invoice.invoice_date,
        redirect_url=reverse("invoicing:invoice_detail", args=[invoice.pk]),
    )


@login_required
@require_POST
@require_company
def invoice_attachment_remove(request, company, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)
    return remove_attachment(
        request,
        company=company,
        document=invoice,
        period_date=invoice.invoice_date,
        redirect_url=reverse("invoicing:invoice_detail", args=[invoice.pk]),
        document_noun="fakturan",
    )


@login_required
@require_POST
@require_company
def invoice_book(request, company, invoice_id):

    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)

    if invoice.is_booked:
        messages.info(request, "Fakturan är redan bokförd.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    run_document_action(
        request, lambda: invoice.bookkeep(request.user), f"Faktura {invoice.invoice_number} har bokförts."
    )
    return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)


@login_required
@require_POST
@require_company
def invoice_register_payment(request, company, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)
    return register_payable_payment_view(
        request, invoice, fallback=reverse("invoicing:invoice_detail", args=[invoice.pk])
    )


@login_required
@require_POST
@require_company
def invoice_offset(request, company, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)
    return offset_payable_view(request, invoice, fallback=reverse("invoicing:invoice_detail", args=[invoice.pk]))


@login_required
@require_POST
@require_company
def invoice_unmark_manually_paid(request, company, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)
    return unmark_payable_manually_paid_view(
        request, invoice, fallback=reverse("invoicing:invoice_detail", args=[invoice.pk])
    )


@login_required
@require_POST
@require_company
def invoice_credit(request, company, invoice_id):

    original_invoice = get_object_or_404(
        Invoice.objects.select_related("customer").prefetch_related("lines__article"),
        pk=invoice_id,
        company=company,
    )

    if original_invoice.is_credit_invoice:
        messages.info(request, "Fakturan är redan en kreditfaktura.")
        return redirect("invoicing:invoice_detail", invoice_id=original_invoice.pk)

    if not original_invoice.is_booked:
        messages.error(request, "Bokför fakturan innan du skapar en kreditfaktura.")
        return redirect("invoicing:invoice_detail", invoice_id=original_invoice.pk)

    lines = list(original_invoice.lines.all())
    if not lines:
        messages.error(request, "Fakturan måste innehålla minst en rad för att kunna krediteras.")
        return redirect("invoicing:invoice_detail", invoice_id=original_invoice.pk)

    today = timezone.localdate()
    credit_invoice = Invoice.objects.create(
        company=company,
        customer=original_invoice.customer,
        invoice_date=today,
        due_date=today,
        payment_terms_days=0,
        currency=original_invoice.currency,
        reference=f"Kreditering av {original_invoice.invoice_number}",
        notes=(
            f"Skapad som kreditfaktura för {original_invoice.invoice_number}."
            if not original_invoice.notes
            else f"Skapad som kreditfaktura för {original_invoice.invoice_number}. {original_invoice.notes}"
        ),
    )

    for line in lines:
        credit_invoice.lines.create(
            article=line.article,
            description=f"Kreditering: {line.description}",
            quantity=(line.quantity or Decimal("0.00")) * Decimal("-1.00"),
            unit=line.unit,
            unit_price=line.unit_price,
            vat_rate=line.vat_rate,
            sort_order=line.sort_order,
        )

    try:
        credit_invoice.bookkeep(request.user)
    except ValidationError as exc:
        messages.warning(
            request, f"Kreditfaktura {credit_invoice.invoice_number} skapades som utkast: {exc.messages[0]}"
        )
    else:
        messages.success(request, f"Kreditfaktura {credit_invoice.invoice_number} skapades och bokfördes.")

    return redirect("invoicing:invoice_detail", invoice_id=credit_invoice.pk)


@login_required
@require_POST
@require_company
def invoice_delete(request, company, invoice_id):

    invoice = get_object_or_404(Invoice, pk=invoice_id, company=company)

    if invoice.is_booked:
        messages.error(request, "Bokförda fakturor kan inte tas bort.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    invoice_label = invoice.invoice_number or f"{invoice.pk}"
    invoice.delete()
    messages.success(request, f"Faktura {invoice_label} har tagits bort.")
    return redirect("invoicing:invoice_list")


def _customer_address_lines(invoice):
    lines = [
        invoice.customer.name,
        invoice.customer.address,
        f"{invoice.customer.postal_code} {invoice.customer.city}".strip(),
    ]
    return [line for line in lines if line and line.strip()]


def _invoice_pdf_context(invoice):
    return {
        "invoice": invoice,
        "logo_size": company_logo_size(invoice.company),
        "payment_qr_png": invoice.build_payment_qr_png(),
        "customer_address_lines": _customer_address_lines(invoice),
        "company_vat_number": (invoice.company.vat_number or "").strip(),
    }


def _reminder_pdf_context(invoice, reminder):
    amount_to_pay = invoice.remaining_amount + reminder.fee
    return {
        "invoice": invoice,
        "reminder_number": reminder.sequence_number,
        "reminder_fee": reminder.fee,
        "pay_by_date": reminder.pay_by_date,
        "amount_to_pay": amount_to_pay,
        "logo_size": company_logo_size(invoice.company),
        "payment_qr_png": invoice.build_payment_qr_png(amount=amount_to_pay, due_date=reminder.pay_by_date),
        "customer_address_lines": _customer_address_lines(invoice),
        "company_vat_number": (invoice.company.vat_number or "").strip(),
    }


@login_required
@require_company
def invoice_print(request, company, invoice_id):

    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "company").prefetch_related("lines"),
        pk=invoice_id,
        company=company,
    )
    return render_pdf_response(
        "invoicing/invoice_print.html",
        _invoice_pdf_context(invoice),
        f"faktura-{invoice.invoice_number or invoice.pk}.pdf",
        disposition="inline",
    )


def _render_reminder_pdf(invoice, reminder):
    return render_pdf_response(
        "invoicing/reminder_print.html",
        _reminder_pdf_context(invoice, reminder),
        f"paminnelse-{invoice.invoice_number or invoice.pk}.pdf",
        disposition="inline",
    )


def _register_reminder_from_post(request, invoice):
    """Guarda och registrera en betalningspåminnelse från POST-data.

    Returnerar (reminder, None) vid framgång, annars (None, redirect-svar)."""
    if not invoice.is_booked or invoice.is_paid or invoice.is_credit_invoice:
        messages.error(request, "Påminnelse kan bara skapas för en bokförd, obetald faktura.")
        return None, redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    form = ReminderPrintForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Ogiltig påminnelseavgift eller ogiltigt datum.")
        return None, redirect("invoicing:invoice_detail", invoice_id=invoice.pk)
    fee = form.cleaned_data["avgift"]
    if fee is None:
        fee = Decimal("0.00")
    pay_by_date = form.cleaned_data["betala_senast"] or (timezone.localdate() + timedelta(days=10))

    reminder = InvoiceReminder.objects.create(
        invoice=invoice,
        fee=fee,
        pay_by_date=pay_by_date,
        created_by=request.user,
    )
    return reminder, None


@login_required
@require_company
@require_POST
def invoice_reminder_print(request, company, invoice_id):
    """Registrera en betalningspåminnelse och returnera den som PDF:
    återstående belopp + valfri avgift, nytt betaldatum."""

    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "company"),
        pk=invoice_id,
        company=company,
    )
    reminder, error_response = _register_reminder_from_post(request, invoice)
    if error_response is not None:
        return error_response
    return _render_reminder_pdf(invoice, reminder)


def _kr(amount):
    return f"{amount:.2f}".replace(".", ",") + " kr"


@login_required
@require_company
@require_POST
def invoice_email(request, company, invoice_id):
    """Skicka fakturan som PDF till kundens e-postadress via företagets utgående konto."""

    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "company").prefetch_related("lines"),
        pk=invoice_id,
        company=company,
    )
    if not invoice.is_booked or invoice.is_credit_invoice:
        messages.error(request, "Bara en bokförd faktura (ej kreditfaktura) kan skickas via e-post.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)
    if not invoice.customer.email:
        messages.error(request, "Kunden saknar e-postadress.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)
    if not company_email_configured(company):
        messages.error(request, "Utgående e-post är inte konfigurerad för företaget.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    try:
        pdf = render_pdf_bytes("invoicing/invoice_print.html", _invoice_pdf_context(invoice))
    except PdfRenderError as exc:
        messages.error(request, str(exc))
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    invoice_label = invoice.invoice_number or invoice.pk
    body = (
        f"Hej {invoice.customer.name}!\n\n"
        f"Här kommer faktura {invoice_label} från {company.name} på {_kr(invoice.total_amount)}, "
        f"att betala senast {invoice.due_date}.\n"
        "Fakturan bifogas som PDF.\n\n"
        f"Vänliga hälsningar\n{company.name}"
    )
    result = send_company_email(
        company,
        purpose=SentEmail.Purpose.INVOICE,
        to=[invoice.customer.email],
        subject=f"Faktura {invoice_label} från {company.name}",
        body=body,
        attachments=[(f"faktura-{invoice_label}.pdf", "application/pdf", pdf)],
        invoice=invoice,
        user=request.user,
    )
    if result.status == SentEmail.Status.SENT:
        messages.success(request, f"Fakturan skickades till {invoice.customer.email}.")
    else:
        messages.error(request, f"Kunde inte skicka fakturan: {result.error}")
    return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)


@login_required
@require_company
@require_POST
def invoice_reminder_email(request, company, invoice_id):
    """Registrera en betalningspåminnelse och skicka den som PDF till kunden."""

    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "company"),
        pk=invoice_id,
        company=company,
    )
    if not invoice.customer.email:
        messages.error(request, "Kunden saknar e-postadress.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)
    if not company_email_configured(company):
        messages.error(request, "Utgående e-post är inte konfigurerad för företaget.")
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    reminder, error_response = _register_reminder_from_post(request, invoice)
    if error_response is not None:
        return error_response

    try:
        pdf = render_pdf_bytes("invoicing/reminder_print.html", _reminder_pdf_context(invoice, reminder))
    except PdfRenderError as exc:
        messages.error(request, str(exc))
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    invoice_label = invoice.invoice_number or invoice.pk
    amount_to_pay = invoice.remaining_amount + reminder.fee
    body = (
        f"Hej {invoice.customer.name}!\n\n"
        f"Detta är en betalningspåminnelse för faktura {invoice_label} från {company.name}.\n"
        f"Belopp att betala: {_kr(amount_to_pay)}, senast {reminder.pay_by_date}.\n"
        "Påminnelsen bifogas som PDF.\n\n"
        f"Vänliga hälsningar\n{company.name}"
    )
    result = send_company_email(
        company,
        purpose=SentEmail.Purpose.REMINDER,
        to=[invoice.customer.email],
        subject=f"Betalningspåminnelse – faktura {invoice_label} från {company.name}",
        body=body,
        attachments=[(f"paminnelse-{invoice_label}.pdf", "application/pdf", pdf)],
        invoice=invoice,
        user=request.user,
    )
    if result.status == SentEmail.Status.SENT:
        messages.success(request, f"Påminnelsen registrerades och skickades till {invoice.customer.email}.")
    else:
        messages.error(request, f"Påminnelsen registrerades, men kunde inte skickas: {result.error}")
    return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)


@login_required
@require_company
def invoice_reminder_reprint(request, company, invoice_id, reminder_id):
    """Skriv ut en redan registrerad påminnelse igen, utan att skapa en ny."""

    reminder = get_object_or_404(
        InvoiceReminder.objects.select_related("invoice__customer", "invoice__company"),
        pk=reminder_id,
        invoice_id=invoice_id,
        invoice__company=company,
    )
    return _render_reminder_pdf(reminder.invoice, reminder)


@login_required
@require_company
def invoice_peppol_xml_download(request, company, invoice_id):
    from django.http import HttpResponse

    from .peppol import PeppolValidationError, generate_peppol_invoice_xml

    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "company").prefetch_related("lines"),
        pk=invoice_id,
        company=company,
    )

    try:
        xml_bytes = generate_peppol_invoice_xml(invoice)
    except PeppolValidationError as exc:
        for error in exc.errors:
            messages.error(request, error)
        return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)

    response = HttpResponse(xml_bytes, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="peppol-{invoice.invoice_number}.xml"'
    return response


@login_required
@require_company
def recurring_invoice_list(request, company):

    recurring_invoices = company.recurring_invoices.select_related("customer").order_by("name")
    return render(request, "invoicing/recurring_invoice_list.html", {"recurring_invoices": recurring_invoices})


@login_required
@require_company
def recurring_invoice_create(request, company):

    article_unit_price_map, article_vat_rate_map, article_meta_map = _build_article_context_maps(company)

    if request.method == "POST":
        form = RecurringInvoiceForm(request.POST, company=company)
        line_formset = build_recurring_invoice_line_formset(company=company, data=request.POST)

        if form.is_valid() and line_formset.is_valid():
            recurring_invoice = form.save(commit=False)
            recurring_invoice.company = company
            recurring_invoice.next_run_date = recurring_invoice.start_date
            recurring_invoice.save()

            created_item_lines = _save_line_formset(
                line_formset, parent=recurring_invoice, parent_field="recurring_invoice"
            )

            if created_item_lines == 0:
                recurring_invoice.delete()
                form.add_error(None, "Lägg till minst en artikelrad med beskrivning, antal och á-pris.")
            else:
                messages.success(request, f'Återkommande faktura "{recurring_invoice.name}" har skapats.')
                return redirect("invoicing:recurring_invoice_detail", recurring_invoice_id=recurring_invoice.pk)
    else:
        form = RecurringInvoiceForm(
            company=company,
            initial={"start_date": timezone.localdate(), "payment_terms_days": 30},
        )
        line_formset = build_recurring_invoice_line_formset(company=company)

    return render(
        request,
        "invoicing/recurring_invoice_form.html",
        {
            "form": form,
            "line_formset": line_formset,
            "article_unit_price_map": article_unit_price_map,
            "article_vat_rate_map": article_vat_rate_map,
            "article_meta_map": article_meta_map,
            "page_title_text": "Ny återkommande faktura",
            "submit_label": "Skapa mall",
        },
    )


@login_required
@require_company
def recurring_invoice_update(request, company, recurring_invoice_id):

    recurring_invoice = get_object_or_404(RecurringInvoice, pk=recurring_invoice_id, company=company)
    article_unit_price_map, article_vat_rate_map, article_meta_map = _build_article_context_maps(company)

    if request.method == "POST":
        form = RecurringInvoiceForm(request.POST, instance=recurring_invoice, company=company)
        line_formset = build_recurring_invoice_line_formset(
            company=company,
            data=request.POST,
            queryset=recurring_invoice.lines.all(),
        )

        if form.is_valid() and line_formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    created_item_lines = _save_line_formset(
                        line_formset, parent=recurring_invoice, parent_field="recurring_invoice"
                    )
                    if created_item_lines == 0:
                        raise _LineValidationAborted
            except _LineValidationAborted:
                form.add_error(None, "Lägg till minst en artikelrad med beskrivning, antal och á-pris.")
            else:
                messages.success(request, f'Återkommande faktura "{recurring_invoice.name}" har uppdaterats.')
                return redirect("invoicing:recurring_invoice_detail", recurring_invoice_id=recurring_invoice.pk)
    else:
        form = RecurringInvoiceForm(instance=recurring_invoice, company=company)
        line_formset = build_recurring_invoice_line_formset(company=company, queryset=recurring_invoice.lines.all())

    return render(
        request,
        "invoicing/recurring_invoice_form.html",
        {
            "form": form,
            "line_formset": line_formset,
            "article_unit_price_map": article_unit_price_map,
            "article_vat_rate_map": article_vat_rate_map,
            "article_meta_map": article_meta_map,
            "page_title_text": "Redigera återkommande faktura",
            "submit_label": "Spara ändringar",
            "recurring_invoice": recurring_invoice,
        },
    )


@login_required
@require_company
def recurring_invoice_detail(request, company, recurring_invoice_id):

    recurring_invoice = get_object_or_404(
        RecurringInvoice.objects.select_related("customer").prefetch_related("lines"),
        pk=recurring_invoice_id,
        company=company,
    )
    generated_invoices = recurring_invoice.generated_invoices.order_by("-invoice_date", "-created_at")

    return render(
        request,
        "invoicing/recurring_invoice_detail.html",
        {
            "recurring_invoice": recurring_invoice,
            "generated_invoices": generated_invoices,
        },
    )


@login_required
@require_POST
@require_company
def recurring_invoice_generate(request, company, recurring_invoice_id):

    recurring_invoice = get_object_or_404(RecurringInvoice, pk=recurring_invoice_id, company=company)

    try:
        invoice = recurring_invoice.generate_invoice()
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("invoicing:recurring_invoice_detail", recurring_invoice_id=recurring_invoice.pk)

    try:
        invoice.bookkeep(request.user)
    except ValidationError as exc:
        messages.warning(request, f"Faktura {invoice.invoice_number} skapades som utkast: {exc.messages[0]}")
    else:
        messages.success(
            request,
            f"Faktura {invoice.invoice_number} skapades och bokfördes för perioden {invoice.recurring_period_label}.",
        )

    return redirect("invoicing:invoice_detail", invoice_id=invoice.pk)


@login_required
@require_POST
@require_company
def recurring_invoice_toggle_active(request, company, recurring_invoice_id):

    recurring_invoice = get_object_or_404(RecurringInvoice, pk=recurring_invoice_id, company=company)
    recurring_invoice.is_active = not recurring_invoice.is_active
    recurring_invoice.save(update_fields=["is_active"])

    if recurring_invoice.is_active:
        messages.success(request, f'"{recurring_invoice.name}" har återupptagits.')
    else:
        messages.success(request, f'"{recurring_invoice.name}" har pausats.')

    return redirect("invoicing:recurring_invoice_detail", recurring_invoice_id=recurring_invoice.pk)


@login_required
@require_POST
@require_company
def recurring_invoice_delete(request, company, recurring_invoice_id):

    recurring_invoice = get_object_or_404(RecurringInvoice, pk=recurring_invoice_id, company=company)

    if recurring_invoice.generated_invoices.exists():
        messages.error(request, "Mallar som redan har genererat fakturor kan inte tas bort. Pausa mallen istället.")
        return redirect("invoicing:recurring_invoice_detail", recurring_invoice_id=recurring_invoice.pk)

    name = recurring_invoice.name
    recurring_invoice.delete()
    messages.success(request, f'Återkommande faktura "{name}" har tagits bort.')
    return redirect("invoicing:recurring_invoice_list")
