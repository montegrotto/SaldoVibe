"""Request-level helpers for linking attachments to a document.

Verifikationer, kundfakturor, leverantörsfakturor and utlägg all carry the same
attachment panel: pick files in the separate picker page, come back, link them, unlink
them again from the detail page. The only things that differ per document are how it is
fetched, which date decides whether its period is locked, where to redirect afterwards,
and the noun used in the messages.

`utils.py` stays free of request/response handling; this is where that lives.
"""

from django.contrib import messages
from django.shortcuts import redirect

from bookkeeping.period_locking import is_date_locked

from .models import TransactionAttachment
from .utils import exclude_used_attachments, parse_attachment_ids


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


def add_attachments(request, *, company, document, period_date, redirect_url):
    """Link the attachments picked in the picker to `document`."""
    if is_date_locked(company, period_date):
        messages.error(request, "Perioden är låst. Bilagor kan inte läggas till.")
        return redirect(redirect_url)

    attachments = selectable_attachments(company, parse_attachment_ids(request.POST.get("selected_attachment_ids")))
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

    "Pending" attachments are the ones the user just picked and came back with, but that
    are not linked yet — they arrive as `?selected_attachments=` and are shown as a
    staged selection until the form is posted.
    """
    linked_ids = document.attachments.values_list("pk", flat=True)
    pending_attachments = TransactionAttachment.objects.filter(
        company=company,
        deleted_at__isnull=True,
        id__in=parse_attachment_ids(request.GET.get("selected_attachments")),
    ).exclude(pk__in=linked_ids)

    return {
        "attach_url": attach_url,
        "detach_url": detach_url,
        "return_to": request.path,
        "pending_attachments": pending_attachments,
        "pending_attachment_ids_csv": ",".join(str(a.pk) for a in pending_attachments),
        "period_locked": is_date_locked(company, period_date),
    }
