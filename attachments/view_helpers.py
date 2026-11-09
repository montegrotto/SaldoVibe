"""Request-level helpers for linking attachments to a document.

Verifikationer, kundfakturor, leverantörsfakturor and utlägg all carry the same
attachment panel: pick files in the separate picker page (which posts straight to the
document's add view), unlink them again from the detail page. The only things that differ per document are how it is
fetched, which date decides whether its period is locked, where to redirect afterwards,
and the noun used in the messages.

`utils.py` stays free of request/response handling; this is where that lives.
"""

from django.contrib import messages
from django.shortcuts import redirect

from bookkeeping.period_locking import is_date_locked

from .models import TransactionAttachment
from .utils import exclude_used_attachments, is_safe_return_to, parse_attachment_ids, replace_query_param


def selectable_attachments(company, attachment_ids):
    """The attachments among `attachment_ids` that exist, aren't deleted and aren't already linked.

    `attachment_ids` is an iterable of ints — run the raw form value through
    `parse_attachment_ids` first. Create views need that parsed list anyway, to echo it
    back into the form's hidden field. Returns a queryset, so callers can still order it.
    """
    return exclude_used_attachments(
        TransactionAttachment.objects.filter(
            company=company,
            deleted_at__isnull=True,
            id__in=attachment_ids,
        )
    )


def picker_selection(request, company):
    """The picker-roundtrip state every create form needs: (ids, attachments).

    Ids come from the form's hidden field on POST, or the picker's return query param on
    GET; attachments are the still-selectable ones, newest first, for the selected-list.
    """
    raw_value = (
        request.POST.get("selected_attachment_ids")
        if request.method == "POST"
        else request.GET.get("selected_attachments")
    )
    attachment_ids = parse_attachment_ids(raw_value)
    return attachment_ids, selectable_attachments(company, attachment_ids).order_by("-uploaded_at")


def _keep_return_to(request, redirect_url):
    """Carry the detail page's own `return_to` (its Tillbaka target) through the add/remove POST."""
    return_to = request.GET.get("return_to")
    return replace_query_param(redirect_url, "return_to", return_to if is_safe_return_to(return_to) else "")


def redirect_after_create(request, default):
    """Redirect after a create form saved: back to the safe `return_to` (e.g. bilagelistan), else `default`.

    The create forms post to their own URL, so the GET query's `return_to` is still on the POST.
    """
    return_to = request.GET.get("return_to")
    return redirect(return_to if is_safe_return_to(return_to) else default)


def add_attachments(request, *, company, document, period_date, redirect_url):
    """Link the attachments picked in the picker to `document`."""
    redirect_url = _keep_return_to(request, redirect_url)
    if is_date_locked(company, period_date):
        messages.error(request, "Perioden är låst. Bilagor kan inte läggas till.")
        return redirect(redirect_url)

    attachments = selectable_attachments(
        company, parse_attachment_ids(",".join(request.POST.getlist("attachment_ids")))
    )
    if attachments.exists():
        document.attachments.add(*attachments)
        messages.success(request, "Bilagorna har lagts till.")
    else:
        messages.error(request, "Inga bilagor valda.")

    return redirect(redirect_url)


def remove_attachment(request, *, company, document, period_date, redirect_url, document_noun):
    """Unlink one attachment from `document`.

    `document_noun` is the definite Swedish noun ("verifikationen", "fakturan",
    "utlägget") — it reads as both "bortkopplad från X" and "hittades inte på X".
    """
    redirect_url = _keep_return_to(request, redirect_url)
    if is_date_locked(company, period_date):
        messages.error(request, "Perioden är låst. Bilagor kan inte tas bort.")
        return redirect(redirect_url)

    attachment = document.attachments.filter(pk=request.POST.get("attachment_id")).first()
    if attachment is not None:
        document.attachments.remove(attachment)
        messages.success(request, f"Bilagan har kopplats bort från {document_noun}.")
    else:
        messages.error(request, f"Bilagan kunde inte hittas på {document_noun}.")

    return redirect(redirect_url)


def attachment_panel_context(request, *, company, document, period_date, attach_url, detach_url):
    """Context the shared attachment panel needs on a document's detail page.

    The picker is opened with `attach_url`, so picking an attachment links it at once.
    """
    # The page's own ?return_to (its Tillbaka target) must survive the picker round trip and the POSTs.
    own_return_to = request.GET.get("return_to")
    return {
        "attach_url": replace_query_param(attach_url, "return_to", own_return_to),
        "detach_url": replace_query_param(detach_url, "return_to", own_return_to),
        "return_to": request.get_full_path(),
        "period_locked": is_date_locked(company, period_date),
    }
