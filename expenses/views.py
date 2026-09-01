from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from attachments.utils import is_safe_return_to
from attachments.view_helpers import (
    add_attachments,
    attachment_panel_context,
    picker_selection,
    remove_attachment,
)
from bookkeeping.company_scope import require_company
from bookkeeping.view_utils import (
    payable_payment_context,
    register_payable_payment_view,
    run_document_action,
    unmark_payable_manually_paid_view,
)

from .forms import ExpenseClaimForm
from .models import ExpenseClaim


@login_required
@require_company
def expense_list(request, company):
    claims = (
        ExpenseClaim.objects.filter(company=company)
        .select_related(
            "employee",
            "expense_account",
            "liability_account",
            "payment_account",
            "registered_transaction",
            "payment_transaction",
        )
        .prefetch_related("attachments", "payments__transaction")
    )

    return render(
        request,
        "expenses/expense_list.html",
        {
            "claims": claims,
            "today_date": timezone.localdate(),
        },
    )


@login_required
@require_company
def expense_create(request, company):
    selected_attachment_ids, selected_attachments = picker_selection(request, company)

    if request.method == "POST":
        form = ExpenseClaimForm(request.POST, company=company)
        if form.is_valid():
            claim = form.save(commit=False)
            claim.company = company
            claim.created_by = request.user
            if claim.employee_id:
                claim.person_name = str(claim.employee)
            claim.save()

            if selected_attachments.exists():
                claim.attachments.add(*selected_attachments)

            if "register" in request.POST:
                try:
                    claim.register_and_bookkeep(request.user)
                except ValidationError as exc:
                    messages.error(request, exc.messages[0])
                    return redirect("expenses:expense_list")
                messages.success(request, "Utlägget har registrerats och bokförts.")
            else:
                messages.success(request, "Utlägget har sparats som utkast.")

            return redirect("expenses:expense_list")
    else:
        form = ExpenseClaimForm(company=company, initial={"expense_date": timezone.localdate()})

    picker_return_to = request.get_full_path()

    return render(
        request,
        "expenses/expense_form.html",
        {
            "form": form,
            "selected_attachments": selected_attachments,
            "selected_attachment_ids_csv": ",".join(str(attachment_id) for attachment_id in selected_attachment_ids),
            "picker_return_to": picker_return_to,
        },
    )


@login_required
@require_company
def expense_detail(request, company, claim_id):
    claim = get_object_or_404(
        ExpenseClaim.objects.select_related(
            "employee", "expense_account", "liability_account", "registered_transaction", "payment_transaction"
        ).prefetch_related("attachments", "payments__transaction"),
        pk=claim_id,
        company=company,
    )

    return_to = request.GET.get("return_to")
    back_url = return_to if is_safe_return_to(return_to) else reverse("expenses:expense_list")

    return render(
        request,
        "expenses/expense_detail.html",
        {
            "claim": claim,
            "back_url": back_url,
            **payable_payment_context(claim),
            "today_date": timezone.localdate(),
            **attachment_panel_context(
                request,
                company=company,
                document=claim,
                period_date=claim.expense_date,
                attach_url=reverse("expenses:expense_attachment_add", args=[claim.pk]),
                detach_url=reverse("expenses:expense_attachment_remove", args=[claim.pk]),
            ),
        },
    )


@login_required
@require_POST
@require_company
def expense_attachment_add(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)
    return add_attachments(
        request,
        company=company,
        document=claim,
        period_date=claim.expense_date,
        redirect_url=reverse("expenses:expense_detail", args=[claim.pk]),
    )


@login_required
@require_POST
@require_company
def expense_attachment_remove(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)
    return remove_attachment(
        request,
        company=company,
        document=claim,
        period_date=claim.expense_date,
        redirect_url=reverse("expenses:expense_detail", args=[claim.pk]),
        document_noun="utlägget",
    )


@login_required
@require_POST
@require_company
def expense_register(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)

    if claim.is_registered:
        messages.info(request, "Utlägget är redan bokfört.")
        return redirect("expenses:expense_list")

    run_document_action(
        request,
        lambda: claim.register_and_bookkeep(request.user),
        "Utlägget har registrerats och bokförts.",
    )
    return redirect("expenses:expense_list")


@login_required
@require_POST
@require_company
def expense_register_payment(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)
    return register_payable_payment_view(request, claim, fallback="expenses:expense_list")


@login_required
@require_POST
@require_company
def expense_unmark_manually_paid(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)
    return unmark_payable_manually_paid_view(request, claim, fallback="expenses:expense_list")


@login_required
@require_POST
@require_company
def expense_delete(request, company, claim_id):
    claim = get_object_or_404(ExpenseClaim, pk=claim_id, company=company)

    if claim.is_registered:
        messages.error(request, "Bokförda utlägg kan inte tas bort.")
        return redirect("expenses:expense_list")

    claim.delete()
    messages.success(request, "Utlägget togs bort.")
    return redirect("expenses:expense_list")
