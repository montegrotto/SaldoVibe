"""Microsoft Graph client for reading mail attachments with app-only auth.

Uses the OAuth 2.0 client credentials grant. The Entra application is expected
to have no tenant-wide Graph consent at all -- access to the single bookkeeping
mailbox is granted through RBAC for Applications in Exchange Online
(``New-ManagementRoleAssignment -Role "Application Mail.Read"``), which keeps
the app scoped to one mailbox instead of every mailbox in the tenant. Sending
via :func:`send_mail` additionally requires the ``Application Mail.Send`` role
for the sending mailbox.

This replaces the previous EWS/exchangelib integration: EWS is disabled for
Exchange Online from October 2026 and removed entirely in April 2027.
"""

import base64
import json
import logging
import ssl
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import certifi

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
LOGIN_BASE_URL = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
REQUEST_TIMEOUT = 30

FILE_ATTACHMENT_TYPE = "#microsoft.graph.fileAttachment"

# Graph accepts these folder names directly; anything else is looked up by
# display name. "INBOX" (the IMAP spelling used by the Gmail path) normalises
# to "inbox" here, so the same company setting works for both providers.
WELL_KNOWN_FOLDERS = {
    "archive",
    "clutter",
    "conflicts",
    "conversationhistory",
    "deleteditems",
    "drafts",
    "inbox",
    "junkemail",
    "localfailures",
    "msgfolderroot",
    "outbox",
    "recoverableitemsdeletions",
    "scheduled",
    "searchfolders",
    "sentitems",
    "serverfailures",
    "syncissues",
}


def _ssl_context():
    # Framework Python builds on macOS don't trust the system keychain, so pin
    # certifi's bundle rather than relying on the ambient default.
    return ssl.create_default_context(cafile=certifi.where())


def _build_url(path, params=None):
    if not params:
        return f"{GRAPH_BASE_URL}/{path}"
    # OData parameters contain spaces and commas that must be percent-encoded.
    query = urllib_parse.urlencode(params, quote_via=urllib_parse.quote)
    return f"{GRAPH_BASE_URL}/{path}?{query}"


def _describe_http_error(exc):
    try:
        body = exc.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    try:
        return json.loads(body).get("error", {}).get("message", "")[:300]
    except Exception:
        return body[:300]


def _get(url, access_token, raw=False):
    request = urllib_request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib_request.urlopen(request, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as response:
        payload = response.read()
    return payload if raw else json.loads(payload.decode("utf-8"))


def fetch_access_token(tenant_id, client_id, client_secret):
    """Obtain an app-only Graph access token. Raises ValueError on failure."""
    payload = urllib_parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": GRAPH_SCOPE,
        }
    ).encode("utf-8")
    request = urllib_request.Request(
        f"{LOGIN_BASE_URL}/{tenant_id}/oauth2/v2.0/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=REQUEST_TIMEOUT, context=_ssl_context()) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        detail = _describe_http_error(exc)
        logger.error("Graph token request failed", extra={"status": exc.code, "detail": detail})
        if "AADSTS7000215" in detail or "AADSTS7000222" in detail:
            raise ValueError(
                "Microsoft avvisade Client Secret. Den kan ha gått ut - skapa en ny under "
                "Certificates & secrets i Entra och uppdatera företagsinställningarna."
            ) from exc
        if "AADSTS700016" in detail:
            raise ValueError(
                "Microsoft hittar inte appen i angiven tenant. Kontrollera att Client ID är "
                "Application (client) ID och inte Object ID."
            ) from exc
        raise ValueError(f"Kunde inte hämta åtkomsttoken från Microsoft ({exc.code}).") from exc
    except Exception as exc:
        logger.exception("Graph token request failed")
        raise ValueError("Kunde inte nå Microsoft för att hämta åtkomsttoken.") from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Microsoft returnerade ingen access_token.")
    return access_token


def send_mail(access_token, mailbox, *, subject, body_text, to, attachments=()):
    """Send a plain-text mail from *mailbox* via POST users/{mailbox}/sendMail.

    ``attachments`` is an iterable of ``(filename, content_type, bytes)``.
    Requires the ``Application Mail.Send`` Exchange RBAC role for the mailbox.
    Raises ValueError with a Swedish message on failure.
    """
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body_text},
        "toRecipients": [{"emailAddress": {"address": address}} for address in to],
    }
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": FILE_ATTACHMENT_TYPE,
                "name": filename,
                "contentType": content_type,
                "contentBytes": base64.b64encode(content).decode("ascii"),
            }
            for filename, content_type, content in attachments
        ]
    payload = json.dumps({"message": message, "saveToSentItems": True}).encode("utf-8")

    quoted_mailbox = urllib_parse.quote(mailbox)
    request = urllib_request.Request(
        f"{GRAPH_BASE_URL}/users/{quoted_mailbox}/sendMail",
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=REQUEST_TIMEOUT, context=_ssl_context()):
            pass
    except urllib_error.HTTPError as exc:
        detail = _describe_http_error(exc)
        logger.error("Graph sendMail failed", extra={"status": exc.code, "detail": detail})
        if exc.code == 403:
            raise ValueError(
                "Microsoft nekade utskicket. Kontrollera att appen har Exchange-rollen "
                '"Application Mail.Send" för avsändarbrevlådan.'
            ) from exc
        raise ValueError(f"Kunde inte skicka e-post via Microsoft ({exc.code}). {detail}".strip()) from exc
    except Exception as exc:
        logger.exception("Graph sendMail failed")
        raise ValueError("Kunde inte nå Microsoft för att skicka e-post.") from exc


def resolve_folder(access_token, mailbox, folder):
    """Return a folder id or well-known name usable in a Graph mail path."""
    name = (folder or "inbox").strip()
    normalized = name.lower().replace(" ", "")
    if normalized in WELL_KNOWN_FOLDERS:
        return normalized

    quoted_mailbox = urllib_parse.quote(mailbox)
    url = _build_url(
        f"users/{quoted_mailbox}/mailFolders",
        {"$select": "id,displayName", "$top": "200"},
    )
    try:
        folders = _get(url, access_token).get("value", [])
    except urllib_error.HTTPError as exc:
        logger.error(
            "Could not list mail folders",
            extra={"status": exc.code, "detail": _describe_http_error(exc)},
        )
        raise ValueError(f"Kunde inte lista mappar i brevlådan ({exc.code}).") from exc

    for item in folders:
        if (item.get("displayName") or "").strip().lower() == name.lower():
            return item["id"]

    available = ", ".join(sorted(f.get("displayName", "") for f in folders)) or "(inga)"
    raise ValueError(f"Hittade ingen mapp med namnet '{name}'. Tillgängliga mappar: {available}")


def list_messages_with_attachments(access_token, mailbox, folder_id, max_messages):
    """Return the most recent messages in the folder that carry attachments.

    Graph rejects ``$filter=hasAttachments`` combined with ``$orderby`` ("The
    restriction or sort order is too complex for this operation"), so we sort by
    date and drop attachment-less messages here. Sorting is the half worth
    keeping: filtering without it can pin the scan to the oldest messages in a
    large mailbox, so newly arrived invoices would never be seen.
    """
    quoted_mailbox = urllib_parse.quote(mailbox)
    url = _build_url(
        f"users/{quoted_mailbox}/mailFolders/{folder_id}/messages",
        {
            "$select": "id,subject,receivedDateTime,internetMessageId,hasAttachments",
            "$orderby": "receivedDateTime desc",
            "$top": str(max_messages),
        },
    )
    try:
        messages = _get(url, access_token).get("value", [])
    except urllib_error.HTTPError as exc:
        detail = _describe_http_error(exc)
        logger.error(
            "Could not list messages",
            extra={"status": exc.code, "mailbox": mailbox, "detail": detail},
        )
        if exc.code == 403:
            raise ValueError(
                "Microsoft nekar åtkomst till brevlådan. Kontrollera att RBAC-tilldelningen "
                "'Application Mail.Read' finns och omfattar brevlådan (behörighetscachen kan "
                "ta upp till två timmar)."
            ) from exc
        if exc.code == 404:
            raise ValueError(f"Brevlådan '{mailbox}' hittades inte.") from exc
        raise ValueError(f"Kunde inte lista meddelanden ({exc.code}): {detail}") from exc

    return [message for message in messages if message.get("hasAttachments")]


def list_attachment_metadata(access_token, mailbox, message_id):
    """List attachment metadata for a message, without downloading content."""
    quoted_mailbox = urllib_parse.quote(mailbox)
    url = _build_url(
        f"users/{quoted_mailbox}/messages/{urllib_parse.quote(message_id)}/attachments",
        {"$select": "id,name,contentType,size,isInline"},
    )
    try:
        return _get(url, access_token).get("value", [])
    except urllib_error.HTTPError as exc:
        logger.warning(
            "Could not list attachments",
            extra={"status": exc.code, "detail": _describe_http_error(exc)},
        )
        return []


def download_attachment(access_token, mailbox, message_id, attachment_id):
    """Download a single file attachment's raw bytes, or None on failure."""
    quoted_mailbox = urllib_parse.quote(mailbox)
    url = (
        f"{GRAPH_BASE_URL}/users/{quoted_mailbox}/messages/"
        f"{urllib_parse.quote(message_id)}/attachments/{urllib_parse.quote(attachment_id)}/$value"
    )
    try:
        return _get(url, access_token, raw=True)
    except urllib_error.HTTPError as exc:
        logger.warning(
            "Could not download attachment",
            extra={"status": exc.code, "detail": _describe_http_error(exc)},
        )
        return None


def is_downloadable_file_attachment(attachment):
    """True for real file attachments, excluding embedded items and links."""
    return attachment.get("@odata.type") == FILE_ATTACHMENT_TYPE
