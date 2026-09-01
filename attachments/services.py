import logging

from bookkeeping.period_locking import is_date_locked

from .extraction_client import extract_fields

logger = logging.getLogger(__name__)


def attachment_relevant_dates(attachment):
    """Dates of every record the attachment is linked to, for period-lock checks."""
    dates = list(attachment.transactions.values_list("date", flat=True))
    dates += list(attachment.supplier_invoices.values_list("invoice_date", flat=True))
    dates += list(attachment.expense_claims.values_list("expense_date", flat=True))
    dates += list(attachment.sales_invoices.values_list("invoice_date", flat=True))
    return dates


def is_attachment_period_locked(attachment):
    """An attachment can't be added or removed once any record it's linked to
    falls in a locked accounting period (BFNAR 2013:2) - independent of whether
    that record has been bokförd."""
    return any(is_date_locked(attachment.company, date) for date in attachment_relevant_dates(attachment))


def _fetch_extracted_data(attachment):
    """Best-effort: returns None (never raises) if the integration is off,
    down, times out, or the uploaded file can't be read back - see
    extraction_client's module docstring. Uppladdningen ska ALDRIG stoppas
    av att det här steget - i värsta fall uteblir bara förslaget."""
    if not attachment.file:
        return None
    try:
        attachment.file.open("rb")
        try:
            file_bytes = attachment.file.read()
        finally:
            if not attachment.file.closed:
                attachment.file.close()
        return extract_fields(file_bytes, attachment.file_name, own_company=attachment.company.name)
    except Exception:
        logger.exception("Kunde inte läsa bilagan för ReInvGrabber-anrop: %s", attachment.file_name)
        return None


def save_attachment_with_thumbnail(attachment):
    attachment.save()
    update_fields = []
    if attachment.generate_thumbnail():
        update_fields += ["thumbnail", "thumbnail_generated_at"]

    extracted = _fetch_extracted_data(attachment)
    if extracted is not None:
        attachment.extracted_data = extracted
        update_fields.append("extracted_data")

    if update_fields:
        attachment.save(update_fields=update_fields)


def first_extraction_suggestion(attachments):
    """First selected attachment carrying a usable ReInvGrabber suggestion (see
    extraction_client.py's field shape: datum, leverantör, totalbelopp,
    momsbelopp, fakturanummer, förfallodatum, ocr_referens, valuta), or None.

    Bookkeeping forms (leverantörsfaktura, verifikation) use this to prefill
    fields when the user books an already-uploaded attachment - never
    authoritative, always just a starting point the user can edit. Only the
    first match is used (not merged across attachments): normally one
    attachment is the actual invoice/receipt being booked, any others are
    backup material."""
    for attachment in attachments:
        data = attachment.extracted_data
        if data and any(value not in (None, "") for value in data.values()):
            return data
    return None
