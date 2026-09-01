import hashlib
import imaplib
import logging
from email import message_from_bytes
from email.header import decode_header, make_header
from email.policy import default as default_email_policy

from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename

from . import graph_mail
from .models import TransactionAttachment
from .services import save_attachment_with_thumbnail

logger = logging.getLogger(__name__)

# Email import only accepts PDF. Invoice mails routinely attach layout graphics
# (logos, corner images) as ordinary non-inline attachments, so allowing image
# types here fills the attachment list with junk. Manual upload still accepts
# PNG and JPEG.
EMAIL_ALLOWED_EXTENSIONS = {"pdf"}
PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
GMAIL_IMAP_HOST = "imap.gmail.com"


def _decode_subject(raw_subject):
    if not raw_subject:
        return ""
    try:
        return str(make_header(decode_header(raw_subject))).strip()
    except Exception:
        return str(raw_subject).strip()


def _build_file_name(file_name, content_type, fallback_prefix):
    """Return a safe PDF file name, or "" when the attachment isn't a PDF."""
    sanitized_name = get_valid_filename(file_name or "").strip()
    if not sanitized_name:
        if (content_type or "").split(";")[0].strip().lower() in PDF_CONTENT_TYPES:
            return f"{fallback_prefix}.pdf"
        return ""

    if "." not in sanitized_name:
        return ""

    extension = sanitized_name.rsplit(".", 1)[-1].lower()
    if extension not in EMAIL_ALLOWED_EXTENSIONS:
        return ""
    return sanitized_name


class _ImportCounters:
    def __init__(self):
        self.imported = 0
        self.duplicates = 0
        self.skipped_unsupported = 0
        self.scanned_messages = 0

    def as_dict(self):
        return {
            "imported": self.imported,
            "duplicates": self.duplicates,
            "skipped_unsupported": self.skipped_unsupported,
            "scanned_messages": self.scanned_messages,
        }


def _store_attachment(company, user, counters, payload, file_name, message_id, attachment_id, subject, protocol):
    """Persist one attachment unless its content already exists for the company."""
    content_hash = hashlib.sha256(payload).hexdigest()

    # Deduplicate on content rather than message id: forwarded invoices arrive
    # with a fresh message id but the same PDF. Soft-deleted rows count too, so
    # a deliberately removed attachment doesn't reappear on the next fetch.
    if TransactionAttachment.objects.filter(company=company, content_hash=content_hash).exists():
        counters.duplicates += 1
        logger.debug(
            "Skipped duplicate attachment",
            extra={"company_id": company.id, "message_id": message_id, "content_hash": content_hash},
        )
        return

    attachment = TransactionAttachment(
        company=company,
        uploaded_by=user,
        source=TransactionAttachment.Source.EMAIL,
        source_provider=company.email_fetch_provider,
        source_message_id=message_id[:255],
        source_attachment_id=attachment_id[:255],
        source_email_subject=subject[:255],
        content_hash=content_hash,
    )
    # save=False only writes the file to storage; save_attachment_with_thumbnail below
    # does the model save, plus the same thumbnail + ReInvGrabber extraction step manual
    # uploads get (see attachments/services.py) - e-postimporterade bilagor ska föreslå
    # fält precis som manuellt uppladdade.
    attachment.file.save(file_name, ContentFile(payload), save=False)
    save_attachment_with_thumbnail(attachment)
    counters.imported += 1
    logger.info(
        "Imported email attachment",
        extra={
            "company_id": company.id,
            "message_id": message_id,
            "file_name": file_name,
            "protocol": protocol,
        },
    )


def _import_imap_attachments_for_company(company, user, max_messages, folder):
    counters = _ImportCounters()

    mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST)
    try:
        logger.debug("Connecting to IMAP host", extra={"host": GMAIL_IMAP_HOST, "company_id": company.id})
        mail.login(company.email_fetch_address, company.email_fetch_password)
        logger.debug("IMAP login successful", extra={"company_id": company.id})

        status, _ = mail.select(folder)
        if status != "OK":
            logger.error(
                "Could not open mailbox",
                extra={"company_id": company.id, "mailbox": folder, "status": status},
            )
            raise ValueError(f"Kunde inte öppna mappen '{folder}'.")

        status, data = mail.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            logger.warning(
                "No messages found or search failed",
                extra={"company_id": company.id, "status": status, "mailbox": folder},
            )
            return counters.as_dict()

        message_ids = data[0].split()
        recent_ids = message_ids[-max_messages:]
        logger.info(
            "Messages selected for import",
            extra={
                "company_id": company.id,
                "total_messages": len(message_ids),
                "selected_messages": len(recent_ids),
                "protocol": "imap",
            },
        )

        for message_id in reversed(recent_ids):
            decoded_message_id = message_id.decode("utf-8", errors="ignore")
            counters.scanned_messages += 1
            status, fetched = mail.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK" or not fetched or not fetched[0]:
                logger.warning(
                    "Failed to fetch message",
                    extra={"company_id": company.id, "message_id": decoded_message_id, "status": status},
                )
                continue

            raw_email = fetched[0][1]
            if not raw_email:
                logger.warning(
                    "Fetched message had empty body",
                    extra={"company_id": company.id, "message_id": decoded_message_id},
                )
                continue

            email_message = message_from_bytes(raw_email, policy=default_email_policy)
            source_message_id = (email_message.get("Message-ID") or decoded_message_id).strip()
            source_subject = _decode_subject(email_message.get("Subject"))

            for part_index, part in enumerate(email_message.walk(), start=1):
                if part.get_content_disposition() != "attachment":
                    continue

                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                file_name = _build_file_name(
                    part.get_filename(),
                    part.get_content_type(),
                    fallback_prefix=f"mail-bilaga-{decoded_message_id}-{part_index}",
                )
                if not file_name:
                    counters.skipped_unsupported += 1
                    logger.debug(
                        "Skipped unsupported attachment",
                        extra={
                            "company_id": company.id,
                            "message_id": decoded_message_id,
                            "part_index": part_index,
                            "content_type": part.get_content_type(),
                        },
                    )
                    continue

                _store_attachment(
                    company=company,
                    user=user,
                    counters=counters,
                    payload=payload,
                    file_name=file_name,
                    message_id=source_message_id,
                    attachment_id=f"{decoded_message_id}:{part_index}:{file_name}",
                    subject=source_subject,
                    protocol="imap",
                )
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass

    return counters.as_dict()


def _import_graph_attachments_for_company(company, user, max_messages, folder):
    counters = _ImportCounters()
    mailbox = company.email_fetch_address

    access_token = graph_mail.fetch_access_token(
        tenant_id=company.email_fetch_oauth_tenant_id.strip(),
        client_id=company.email_fetch_oauth_client_id.strip(),
        client_secret=company.email_fetch_oauth_client_secret,
    )
    folder_id = graph_mail.resolve_folder(access_token, mailbox, folder)
    messages = graph_mail.list_messages_with_attachments(access_token, mailbox, folder_id, max_messages)

    logger.info(
        "Messages selected for import",
        extra={"company_id": company.id, "selected_messages": len(messages), "protocol": "graph"},
    )

    for message in messages:
        counters.scanned_messages += 1
        graph_message_id = message.get("id") or ""
        source_message_id = (message.get("internetMessageId") or graph_message_id).strip()
        source_subject = (message.get("subject") or "").strip()

        for attachment in graph_mail.list_attachment_metadata(access_token, mailbox, graph_message_id):
            if not graph_mail.is_downloadable_file_attachment(attachment):
                continue

            file_name = _build_file_name(
                attachment.get("name"),
                attachment.get("contentType"),
                fallback_prefix=f"mail-bilaga-{attachment.get('id', '')[:20]}",
            )
            if not file_name:
                counters.skipped_unsupported += 1
                logger.debug(
                    "Skipped unsupported attachment",
                    extra={
                        "company_id": company.id,
                        "message_id": source_message_id,
                        "attachment_name": attachment.get("name"),
                        "content_type": attachment.get("contentType"),
                    },
                )
                continue

            payload = graph_mail.download_attachment(
                access_token, mailbox, graph_message_id, attachment.get("id") or ""
            )
            if not payload:
                continue

            _store_attachment(
                company=company,
                user=user,
                counters=counters,
                payload=payload,
                file_name=file_name,
                message_id=source_message_id,
                attachment_id=f"{graph_message_id}:{attachment.get('id', '')}",
                subject=source_subject,
                protocol="graph",
            )

    return counters.as_dict()


def import_email_attachments_for_company(company, user, max_messages=100):
    if not company.email_fetch_enabled:
        raise ValueError("E-posthämtning är inte aktiverad för företaget.")
    if company.email_fetch_provider not in {"gmail", "outlook"}:
        raise ValueError("Välj Gmail eller Outlook som e-postleverantör i företagsinställningarna.")
    if not company.email_fetch_address:
        raise ValueError("Ange e-postkonto i företagsinställningarna.")

    if company.email_fetch_provider == "gmail" and not company.email_fetch_password:
        raise ValueError("Ange app-lösenord för Gmail i företagsinställningarna.")

    if company.email_fetch_provider == "outlook":
        missing = [
            label
            for label, value in (
                ("Tenant ID", company.email_fetch_oauth_tenant_id),
                ("Client ID", company.email_fetch_oauth_client_id),
                ("Client Secret", company.email_fetch_oauth_client_secret),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise ValueError(f"Outlook kräver {', '.join(missing)} i företagsinställningarna.")

    folder = company.email_fetch_folder or "INBOX"

    logger.info(
        "Starting email attachment import",
        extra={
            "company_id": company.id,
            "provider": company.email_fetch_provider,
            "mailbox": folder,
            "email_address": company.email_fetch_address,
            "max_messages": max_messages,
        },
    )

    try:
        if company.email_fetch_provider == "gmail":
            result = _import_imap_attachments_for_company(
                company=company, user=user, max_messages=max_messages, folder=folder
            )
        else:
            result = _import_graph_attachments_for_company(
                company=company, user=user, max_messages=max_messages, folder=folder
            )

        logger.info("Email attachment import finished", extra={"company_id": company.id, **result})
        return result
    except Exception:
        logger.exception(
            "Email attachment import failed",
            extra={
                "company_id": company.id,
                "provider": company.email_fetch_provider,
                "mailbox": folder,
            },
        )
        raise
