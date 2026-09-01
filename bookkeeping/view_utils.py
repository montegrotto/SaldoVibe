"""Shared view plumbing for the payable document apps (invoicing, supplier_invoices, expenses).

Mirrors `attachments/view_helpers.py`: the app views fetch their document and delegate here,
so the message/redirect choreography exists once. Wording comes from the model's
`PAYMENT_LABELS` (see `bookkeeping/payables.py`).
"""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils import timezone

from attachments.utils import is_safe_return_to

from .forms import RegisterPaymentForm
from .payables import offset_payables, offsettable_counterparts, register_manual_payment


def run_document_action(request, action, success_message):
    """Run a document action, flashing ValidationError as an error and success_message otherwise."""
    try:
        action()
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, success_message)


def redirect_return_to(request, fallback):
    """Redirect to the posted return_to when it is a safe local path, otherwise to fallback."""
    return_to = request.POST.get("return_to")
    return redirect(return_to if is_safe_return_to(return_to) else fallback)


def payable_payment_context(payable):
    """Detail-page context for the payment card: the register-payment form and kvittning candidates."""
    if not payable.is_bookkept or payable.is_paid:
        return {"register_payment_form": None, "offset_candidates": []}
    return {
        "register_payment_form": RegisterPaymentForm(payable=payable),
        "offset_candidates": offsettable_counterparts(payable),
    }


def register_payable_payment_view(request, payable, *, fallback):
    """POST handler behind every "Registrera betalning" form."""
    form = RegisterPaymentForm(request.POST, payable=payable)
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()))[0])
        return redirect_return_to(request, fallback)

    run_document_action(
        request,
        lambda: register_manual_payment(
            payable,
            request.user,
            payment_date=form.cleaned_data["payment_date"],
            amount=form.cleaned_data.get("amount") or Decimal("0.00"),
            payment_account=form.cleaned_data.get("payment_account"),
            write_off_amount=form.cleaned_data.get("write_off_amount") or Decimal("0.00"),
            write_off_account=form.cleaned_data.get("write_off_account"),
            adjust_vat=form.cleaned_data.get("adjust_vat", False),
        ),
        payable.PAYMENT_LABELS.payment_registered,
    )
    return redirect_return_to(request, fallback)


def offset_payable_view(request, payable, *, fallback):
    """POST handler behind the "Kvitta mot faktura" form: counterpart id + payment_date."""
    counterpart = next(
        (
            candidate
            for candidate in offsettable_counterparts(payable)
            if str(candidate.pk) == request.POST.get("counterpart")
        ),
        None,
    )
    if counterpart is None:
        messages.error(request, "Välj en faktura att kvitta mot.")
        return redirect_return_to(request, fallback)

    try:
        payment_date = timezone.datetime.strptime(request.POST.get("payment_date", ""), "%Y-%m-%d").date()
    except ValueError:
        payment_date = timezone.localdate()

    run_document_action(
        request,
        lambda: offset_payables(payable, counterpart, request.user, payment_date=payment_date),
        "Fakturorna har kvittats mot varandra.",
    )
    return redirect_return_to(request, fallback)


def unmark_payable_manually_paid_view(request, payable, *, fallback):
    run_document_action(
        request,
        lambda: payable.unmark_manually_paid(request.user),
        payable.PAYMENT_LABELS.unmarked_paid,
    )
    return redirect_return_to(request, fallback)
