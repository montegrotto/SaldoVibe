import logging
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from auditlog.context import audit_user
from bookkeeping.company_scope import can_access_company, is_read_only_member, require_company

from .forms import TransactionAttachmentForm
from .models import AttachmentUploadToken, TransactionAttachment
from .services import is_attachment_period_locked, save_attachment_with_thumbnail
from .utils import exclude_used_attachments, is_safe_return_to, parse_attachment_ids, replace_query_param

logger = logging.getLogger(__name__)

NEW_UPLOAD_TOKEN_SESSION_KEY = "new_attachment_upload_token"


company_required = require_company(logger=logger, log_message="Active company missing in attachment flow")


@login_required
@company_required
def attachment_list(request, company):

    logger.debug(
        "Attachment list opened",
        extra={"company_id": company.id, "user_id": request.user.id, "method": request.method},
    )

    if request.method == "POST":
        attachment_form = TransactionAttachmentForm(request.POST, request.FILES)
        if attachment_form.is_valid():
            attachment = attachment_form.save(commit=False)
            attachment.company = company
            attachment.uploaded_by = request.user
            save_attachment_with_thumbnail(attachment)
            logger.info(
                "Attachment uploaded",
                extra={
                    "company_id": company.id,
                    "user_id": request.user.id,
                    "attachment_id": attachment.id,
                    "file_name": attachment.file_name,
                },
            )
            messages.success(request, "Bilagan har laddats upp.")
            return redirect("attachments:attachment_list")
        logger.warning(
            "Attachment upload failed validation",
            extra={"company_id": company.id, "user_id": request.user.id, "errors": str(attachment_form.errors)},
        )
        messages.error(request, "Kunde inte ladda upp bilagan. Kontrollera filformat.")
    else:
        attachment_form = TransactionAttachmentForm()

    attachments = exclude_used_attachments(
        TransactionAttachment.objects.filter(company=company, deleted_at__isnull=True)
    ).order_by("-uploaded_at")
    return render(
        request,
        "attachments/attachment_list.html",
        {
            "attachment_form": attachment_form,
            "attachments": attachments,
            "upload_token": AttachmentUploadToken.objects.filter(company=company, user=request.user).first(),
            # Klartexten finns bara i sessionen fram till den här visningen.
            "new_upload_token": request.session.pop(NEW_UPLOAD_TOKEN_SESSION_KEY, None),
            "upload_api_url": request.build_absolute_uri(reverse("attachments:attachment_api_upload")),
        },
    )


@login_required
@company_required
@require_POST
def attachment_upload_token(request, company):
    if "revoke" in request.POST:
        AttachmentUploadToken.objects.filter(company=company, user=request.user).delete()
        messages.success(request, "Uppladdningstoken har återkallats.")
    else:
        request.session[NEW_UPLOAD_TOKEN_SESSION_KEY] = AttachmentUploadToken.issue(company, request.user)
    logger.info(
        "Attachment upload token changed",
        extra={"company_id": company.id, "user_id": request.user.id, "revoked": "revoke" in request.POST},
    )
    return redirect("attachments:attachment_list")


def _resolve_upload_token(request):
    scheme, _, raw_token = request.headers.get("Authorization", "").partition(" ")
    raw_token = raw_token.strip()
    if scheme.lower() != "bearer" or not raw_token:
        return None
    token = (
        AttachmentUploadToken.objects.select_related("company", "user")
        .filter(token_hash=AttachmentUploadToken.hash_token(raw_token))
        .first()
    )
    if token is None:
        return None
    # A token never outlives the rights of the user it was issued to.
    company, user = token.company, token.user
    if not (user.is_active and company.is_active and can_access_company(user, company)):
        return None
    if is_read_only_member(user, company):
        return None
    return token


# Token-authenticated and cookie-free (called from e.g. an iOS shortcut), so
# there is no session for CSRF to protect.
@csrf_exempt
@require_POST
def attachment_api_upload(request):
    token = _resolve_upload_token(request)
    if token is None:
        logger.warning("Attachment API upload rejected: invalid token")
        return JsonResponse({"error": "Ogiltig eller återkallad token."}, status=401)

    attachment_form = TransactionAttachmentForm(request.POST, request.FILES)
    if not attachment_form.is_valid():
        logger.warning(
            "Attachment API upload failed validation",
            extra={"company_id": token.company_id, "user_id": token.user_id, "errors": str(attachment_form.errors)},
        )
        errors = [error for field_errors in attachment_form.errors.values() for error in field_errors]
        return JsonResponse({"error": " ".join(errors)}, status=400)

    attachment = attachment_form.save(commit=False)
    attachment.company = token.company
    attachment.uploaded_by = token.user
    with audit_user(token.user):
        save_attachment_with_thumbnail(attachment)
    logger.info(
        "Attachment uploaded via token",
        extra={
            "company_id": token.company_id,
            "user_id": token.user_id,
            "attachment_id": attachment.id,
            "file_name": attachment.file_name,
        },
    )
    return JsonResponse({"id": attachment.id, "file_name": attachment.file_name}, status=201)


@login_required
@company_required
def attachment_picker(request, company):

    default_return_to = reverse("supplier_invoices:invoice_create")
    incoming_return_to = request.GET.get("return_to") if request.method == "GET" else request.POST.get("return_to")
    return_to = incoming_return_to if is_safe_return_to(incoming_return_to) else default_return_to

    if request.method == "POST":
        if "upload" in request.POST:
            attachment_form = TransactionAttachmentForm(request.POST, request.FILES)
            selected_ids = parse_attachment_ids(request.POST.get("selected_attachment_ids"))
            if attachment_form.is_valid():
                attachment = attachment_form.save(commit=False)
                attachment.company = company
                attachment.uploaded_by = request.user
                save_attachment_with_thumbnail(attachment)
                messages.success(request, "Bilagan har laddats upp.")
                selected_ids.append(attachment.id)
                selected_ids = list(dict.fromkeys(selected_ids))
                picker_url = reverse("attachments:attachment_picker")
                return redirect(
                    f"{picker_url}?{urlencode({'return_to': return_to, 'selected': ','.join(str(v) for v in selected_ids)})}"
                )
            messages.error(request, "Kunde inte ladda upp bilagan. Kontrollera filformat.")
        else:
            posted_ids = [value for value in request.POST.getlist("attachment_ids") if value.isdigit()]
            selected_ids = list(
                exclude_used_attachments(
                    TransactionAttachment.objects.filter(
                        company=company,
                        deleted_at__isnull=True,
                        id__in=posted_ids,
                    )
                ).values_list("id", flat=True)
            )
            return redirect(
                replace_query_param(return_to, "selected_attachments", ",".join(str(v) for v in selected_ids))
            )
    else:
        attachment_form = TransactionAttachmentForm()
        selected_ids = parse_attachment_ids(request.GET.get("selected"))

    if request.method == "POST" and "upload" not in request.POST:
        selected_ids = []

    attachments = exclude_used_attachments(
        TransactionAttachment.objects.filter(company=company, deleted_at__isnull=True)
    ).order_by("-uploaded_at")

    return render(
        request,
        "attachments/attachment_picker.html",
        {
            "attachment_form": attachment_form,
            "attachments": attachments,
            "selected_ids": selected_ids,
            "selected_ids_csv": ",".join(str(v) for v in selected_ids),
            "return_to": return_to,
        },
    )


@login_required
@require_POST
@company_required
def attachment_delete(request, company, attachment_id):

    incoming_return_to = request.POST.get("return_to")
    return_to = incoming_return_to if is_safe_return_to(incoming_return_to) else reverse("attachments:attachment_list")

    attachment = TransactionAttachment.objects.filter(pk=attachment_id, company=company).first()
    if attachment is None:
        messages.error(request, "Bilagan kunde inte hittas för aktivt företag.")
        return redirect(return_to)
    if is_attachment_period_locked(attachment):
        messages.error(request, "Bilagan tillhör en låst period och kan inte tas bort.")
        return redirect(return_to)

    if attachment.deleted_at is not None:
        messages.info(request, "Bilagan är redan borttagen.")
        return redirect(return_to)

    delete_reason = (request.POST.get("delete_reason") or "").strip()
    logger.info(
        "Attachment soft-deleted",
        extra={
            "company_id": company.id,
            "user_id": request.user.id,
            "attachment_id": attachment.id,
            "file_name": attachment.file_name,
            "delete_reason": delete_reason,
        },
    )
    attachment.deleted_at = timezone.now()
    attachment.deleted_by = request.user
    attachment.delete_reason = delete_reason
    attachment.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])
    messages.success(request, "Bilagan har markerats som borttagen.")

    return redirect(return_to)


@login_required
@company_required
@xframe_options_sameorigin
def attachment_preview(request, company, attachment_id):

    attachment = TransactionAttachment.objects.filter(
        pk=attachment_id,
        company=company,
        deleted_at__isnull=True,
    ).first()
    if attachment is None:
        return HttpResponseNotFound("Bilagan hittades inte.")

    file_name = attachment.file_name
    content_type, _ = mimetypes.guess_type(file_name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(attachment.file.open("rb"), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{file_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@company_required
def attachment_thumbnail(request, company, attachment_id):

    attachment = TransactionAttachment.objects.filter(
        pk=attachment_id,
        company=company,
        deleted_at__isnull=True,
    ).first()
    if attachment is None:
        return HttpResponseNotFound("Bilagan hittades inte.")

    if not attachment.thumbnail:
        if attachment.generate_thumbnail():
            attachment.save(update_fields=["thumbnail", "thumbnail_generated_at"])
        else:
            return HttpResponseNotFound("Miniatyr saknas.")
    elif attachment.file_name.lower().endswith(".pdf") and attachment.is_legacy_pdf_placeholder_thumbnail():
        if attachment.generate_thumbnail():
            attachment.save(update_fields=["thumbnail", "thumbnail_generated_at"])

    response = FileResponse(attachment.thumbnail.open("rb"), content_type="image/jpeg")
    response["Content-Disposition"] = f'inline; filename="{attachment.thumbnail_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
