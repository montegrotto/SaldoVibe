from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from attachments.utils import is_safe_return_to
from bookkeeping.company_scope import require_active_company, require_company
from bookkeeping.context_processors import (
    FIXED_ASSET_ALERT_ACK_SESSION_KEY,
    get_topbar_alert_state_for_company,
)

from .forms import (
    FixedAssetDepreciationCorrectionForm,
    FixedAssetDisposalForm,
    FixedAssetForm,
    FixedAssetImpairmentCorrectionForm,
    FixedAssetImpairmentForm,
    FixedAssetTypeForm,
)
from .models import (
    FixedAsset,
    FixedAssetDepreciation,
    FixedAssetImpairment,
    FixedAssetReclassification,
    FixedAssetType,
    ensure_default_asset_types,
)


@login_required
@require_company
def asset_list(request, company):

    ensure_default_asset_types(company)

    assets = company.fixed_assets.order_by("-is_active", "name")
    due_assets = [asset for asset in assets if asset.is_active and asset.is_due_for_depreciation]

    return render(
        request,
        "fixed_assets/asset_list.html",
        {
            "assets": assets,
            "due_assets": due_assets,
        },
    )


@login_required
@require_company
def asset_create(request, company):

    ensure_default_asset_types(company)

    if request.method == "POST":
        form = FixedAssetForm(request.POST, company=company)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.company = company
            asset.save()
            messages.success(request, "Anläggningstillgången har skapats.")
            return redirect("fixed_assets:asset_list")
    else:
        form = FixedAssetForm(company=company, initial={"is_active": True})

    return render(
        request,
        "fixed_assets/asset_form.html",
        {
            "form": form,
            "page_title_text": "Ny anläggningstillgång",
            "submit_label": "Skapa tillgång",
        },
    )


@login_required
@require_company
def asset_update(request, company, pk):

    ensure_default_asset_types(company)

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    if request.method == "POST":
        previous_asset_type = asset.asset_type
        form = FixedAssetForm(request.POST, instance=asset, company=company)
        if form.is_valid():
            saved_asset = form.save()
            new_asset_type = saved_asset.asset_type
            if new_asset_type != previous_asset_type:
                FixedAssetReclassification.objects.create(
                    fixed_asset=saved_asset,
                    from_asset_type=previous_asset_type,
                    to_asset_type=new_asset_type,
                    reason=form.cleaned_data.get("reclassification_reason", ""),
                    changed_by=request.user,
                )
            messages.success(request, "Anläggningstillgången har uppdaterats.")
            return redirect("fixed_assets:asset_list")
    else:
        form = FixedAssetForm(instance=asset, company=company)

    return render(
        request,
        "fixed_assets/asset_form.html",
        {
            "form": form,
            "asset": asset,
            "page_title_text": "Redigera anläggningstillgång",
            "submit_label": "Spara ändringar",
        },
    )


@login_required
@require_POST
@require_company
def asset_delete(request, company, pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    if asset.depreciation_entries.exists() or asset.impairment_entries.exists():
        messages.error(
            request,
            "Tillgången har bokförda avskrivningar eller nedskrivningar och kan inte raderas. "
            "Avyttra/utrangera tillgången i stället för att bevara historiken.",
        )
        return redirect("fixed_assets:asset_detail", pk=asset.pk)

    asset_name = asset.name
    asset.delete()
    messages.success(request, f"Anläggningstillgången '{asset_name}' har raderats.")
    return redirect("fixed_assets:asset_list")


@login_required
@require_company
def asset_detail(request, company, pk):

    asset = get_object_or_404(
        FixedAsset.objects.prefetch_related("depreciation_entries", "impairment_entries", "reclassification_entries"),
        pk=pk,
        company=company,
    )
    impairment_form = FixedAssetImpairmentForm()
    disposal_form = FixedAssetDisposalForm(company=company)
    return_to = request.GET.get("return_to")
    back_url = return_to if is_safe_return_to(return_to) else reverse("fixed_assets:asset_list")
    return render(
        request,
        "fixed_assets/asset_detail.html",
        {
            "asset": asset,
            "impairment_form": impairment_form,
            "disposal_form": disposal_form,
            "back_url": back_url,
        },
    )


@login_required
@require_POST
@require_company
def asset_register_impairment(request, company, pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    form = FixedAssetImpairmentForm(request.POST)
    if form.is_valid():
        try:
            asset.register_impairment(
                period=form.cleaned_data["period"],
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else "Kunde inte registrera nedskrivning.")
        else:
            messages.success(request, "Nedskrivningen har registrerats.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)

    return redirect("fixed_assets:asset_detail", pk=asset.pk)


@login_required
@require_POST
@require_company
def asset_correct_depreciation(request, company, pk, entry_pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    original_entry = get_object_or_404(FixedAssetDepreciation, pk=entry_pk, fixed_asset=asset)
    form = FixedAssetDepreciationCorrectionForm(request.POST)
    if form.is_valid():
        try:
            asset.register_depreciation_correction(
                original_entry=original_entry,
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else "Kunde inte registrera korrigering.")
        else:
            messages.success(request, "Korrigeringen har registrerats.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)

    return redirect("fixed_assets:asset_detail", pk=asset.pk)


@login_required
@require_POST
@require_company
def asset_correct_impairment(request, company, pk, entry_pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    original_entry = get_object_or_404(FixedAssetImpairment, pk=entry_pk, fixed_asset=asset)
    form = FixedAssetImpairmentCorrectionForm(request.POST)
    if form.is_valid():
        try:
            asset.register_impairment_correction(
                original_entry=original_entry,
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                user=request.user,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else "Kunde inte registrera korrigering.")
        else:
            messages.success(request, "Korrigeringen har registrerats.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)

    return redirect("fixed_assets:asset_detail", pk=asset.pk)


@login_required
@require_POST
@require_company
def asset_dispose(request, company, pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    form = FixedAssetDisposalForm(request.POST, company=company)
    if form.is_valid():
        try:
            asset.dispose(
                disposal_date=form.cleaned_data["disposal_date"],
                disposal_type=form.cleaned_data["disposal_type"],
                reason=form.cleaned_data["disposal_reason"],
                user=request.user,
                sale_price=form.cleaned_data["sale_price"],
                proceeds_account=form.cleaned_data["proceeds_account"],
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else "Kunde inte registrera avgång.")
        else:
            messages.success(
                request,
                f"Anläggningstillgången '{asset.name}' har avyttrats/utrangerats och bokförts "
                f"(verifikation {asset.disposal_transaction.voucher_series}{asset.disposal_transaction.voucher_number}).",
            )
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)

    return redirect("fixed_assets:asset_detail", pk=asset.pk)


@login_required
@require_company
def asset_type_list(request, company):

    ensure_default_asset_types(company)
    asset_types = company.fixed_asset_types.select_related(
        "depreciation_expense_account", "accumulated_depreciation_account"
    ).order_by("sort_order", "name")
    return render(request, "fixed_assets/asset_type_list.html", {"asset_types": asset_types})


@login_required
@require_company
def asset_type_create(request, company):

    if request.method == "POST":
        form = FixedAssetTypeForm(request.POST, company=company)
        if form.is_valid():
            asset_type = form.save(commit=False)
            asset_type.company = company
            asset_type.save()
            messages.success(request, "Tillgångstypen har skapats.")
            return redirect("fixed_assets:asset_type_list")
    else:
        form = FixedAssetTypeForm(company=company, initial={"is_active": True})

    return render(
        request,
        "fixed_assets/asset_type_form.html",
        {
            "form": form,
            "page_title_text": "Ny tillgångstyp",
            "submit_label": "Skapa tillgångstyp",
        },
    )


@login_required
@require_company
def asset_type_update(request, company, pk):

    asset_type = get_object_or_404(FixedAssetType, pk=pk, company=company)
    if request.method == "POST":
        form = FixedAssetTypeForm(request.POST, instance=asset_type, company=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Tillgångstypen har uppdaterats.")
            return redirect("fixed_assets:asset_type_list")
    else:
        form = FixedAssetTypeForm(instance=asset_type, company=company)

    return render(
        request,
        "fixed_assets/asset_type_form.html",
        {
            "form": form,
            "asset_type": asset_type,
            "page_title_text": "Redigera tillgångstyp",
            "submit_label": "Spara ändringar",
        },
    )


@login_required
@require_POST
@require_company
def asset_run_depreciation(request, company, pk):

    asset = get_object_or_404(FixedAsset, pk=pk, company=company)
    try:
        depreciation = asset.register_monthly_depreciation(user=request.user)
    except ValidationError as exc:
        message = exc.messages[0] if exc.messages else "Kunde inte registrera avskrivning."
        if "inget återstående belopp" in message.lower():
            message += " Kontrollera att anskaffningsvärde är högre än restvärde."
        messages.error(request, message)
    else:
        messages.success(
            request,
            f"Avskrivning registrerad för {depreciation.period}: {depreciation.amount} kr.",
        )

    return redirect("fixed_assets:asset_list")


@login_required
@require_POST
def acknowledge_alerts(request):
    company = require_active_company(request)
    if company is None:
        return JsonResponse({"ok": False, "error": "no-company"}, status=403)

    due_signature = get_topbar_alert_state_for_company(company)["topbar_alert_signature"]
    ack_map = request.session.get(FIXED_ASSET_ALERT_ACK_SESSION_KEY, {})
    ack_map[str(company.pk)] = due_signature
    request.session[FIXED_ASSET_ALERT_ACK_SESSION_KEY] = ack_map

    return JsonResponse({"ok": True})
