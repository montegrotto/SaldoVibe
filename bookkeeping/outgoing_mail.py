"""Centraliserad utgående e-post.

Kundriktad e-post (fakturor, påminnelser) skickas via företagets utgående
konto (SMTP eller Microsoft Graph, konfigurerat i företagsinställningarna).
Systemmail (notisdigest) skickas via företagets notiskonto om ett är valt —
eget SMTP/Graph-konto eller samma som utgående — annars via det globala
kontot i EMAIL_*-settings. Varje utskick loggas som en SentEmail-rad —
funktionerna kastar aldrig, kontrollera radens status.
"""

import logging

from django.conf import settings
from django.core import mail as django_mail

from attachments import graph_mail

from .models import Company, SentEmail

logger = logging.getLogger(__name__)


def _smtp_configured(company, prefix):
    return bool(getattr(company, f"{prefix}smtp_host") and getattr(company, f"{prefix}from"))


def _graph_configured(company, prefix):
    return bool(
        company.email_fetch_oauth_tenant_id
        and company.email_fetch_oauth_client_id
        and company.email_fetch_oauth_client_secret
        and (getattr(company, f"{prefix}from") or company.email_fetch_address)
    )


def company_email_configured(company):
    """Kan företaget skicka kundriktad e-post?"""
    provider = company.email_send_provider
    if provider == Company.EmailSendProvider.SMTP:
        return _smtp_configured(company, "email_send_")
    if provider == Company.EmailSendProvider.GRAPH:
        return _graph_configured(company, "email_send_")
    return False


def _send_smtp(company, prefix, *, subject, body, to, attachments):
    port = getattr(company, f"{prefix}smtp_port")
    connection = django_mail.get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=getattr(company, f"{prefix}smtp_host"),
        port=port,
        username=getattr(company, f"{prefix}smtp_username"),
        password=getattr(company, f"{prefix}smtp_password"),
        use_tls=getattr(company, f"{prefix}smtp_use_tls") and port != 465,
        use_ssl=port == 465,
    )
    message = django_mail.EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(company, f"{prefix}from"),
        to=to,
        connection=connection,
    )
    for filename, content_type, content in attachments:
        message.attach(filename, content, content_type)
    message.send()


def _send_graph(company, prefix, *, subject, body, to, attachments):
    access_token = graph_mail.fetch_access_token(
        company.email_fetch_oauth_tenant_id,
        company.email_fetch_oauth_client_id,
        company.email_fetch_oauth_client_secret,
    )
    graph_mail.send_mail(
        access_token,
        getattr(company, f"{prefix}from") or company.email_fetch_address,
        subject=subject,
        body_text=body,
        to=to,
        attachments=attachments,
    )


def _send_via_company_account(company, provider, prefix, *, subject, body, to, attachments):
    if provider == Company.EmailSendProvider.SMTP:
        if not _smtp_configured(company, prefix):
            raise ValueError("SMTP-kontot är inte färdigkonfigurerat (server och avsändaradress krävs).")
        _send_smtp(company, prefix, subject=subject, body=body, to=to, attachments=attachments)
    elif provider == Company.EmailSendProvider.GRAPH:
        if not _graph_configured(company, prefix):
            raise ValueError("Microsoft 365-kontot är inte färdigkonfigurerat (uppgifter och brevlåda krävs).")
        _send_graph(company, prefix, subject=subject, body=body, to=to, attachments=attachments)
    else:
        raise ValueError("Utgående e-post är inte konfigurerad för företaget.")


def _log(company, *, purpose, to, subject, error, invoice, user):
    return SentEmail.objects.create(
        company=company,
        purpose=purpose,
        recipient=", ".join(to)[:254],
        subject=subject[:255],
        status=SentEmail.Status.FAILED if error else SentEmail.Status.SENT,
        error=error,
        invoice=invoice,
        created_by=user,
    )


def send_company_email(company, *, purpose, to, subject, body, attachments=(), invoice=None, user=None):
    """Skicka via företagets utgående konto. Returnerar SentEmail-raden."""
    error = ""
    try:
        _send_via_company_account(
            company,
            company.email_send_provider,
            "email_send_",
            subject=subject,
            body=body,
            to=to,
            attachments=attachments,
        )
    except Exception as exc:
        logger.exception("Utskick misslyckades för företag %s", company.pk)
        error = str(exc)[:2000]
    return _log(company, purpose=purpose, to=to, subject=subject, error=error, invoice=invoice, user=user)


def send_system_email(company, *, purpose, to, subject, body):
    """Skicka systemmail (notiser) via företagets notiskonto, annars det
    globala kontot i EMAIL_*-settings. Returnerar SentEmail-raden."""
    error = ""
    try:
        notify_provider = company.email_notify_provider
        if notify_provider == Company.EmailNotifyProvider.OUTGOING:
            _send_via_company_account(
                company, company.email_send_provider, "email_send_", subject=subject, body=body, to=to, attachments=()
            )
        elif notify_provider in (Company.EmailNotifyProvider.SMTP, Company.EmailNotifyProvider.GRAPH):
            _send_via_company_account(
                company, notify_provider, "email_notify_", subject=subject, body=body, to=to, attachments=()
            )
        else:
            django_mail.send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    except Exception as exc:
        logger.exception("Systemutskick misslyckades för företag %s", company.pk)
        error = str(exc)[:2000]
    return _log(company, purpose=purpose, to=to, subject=subject, error=error, invoice=None, user=None)
