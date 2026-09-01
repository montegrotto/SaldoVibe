"""Central sändmodul: dispatch per provider, loggning, aldrig raise."""

from unittest.mock import patch

from django.core import mail
from django.test import override_settings

from bookkeeping.models import Company, SentEmail
from bookkeeping.outgoing_mail import company_email_configured, send_company_email, send_system_email
from saldovibe.testing import CompanyTestCase

SMTP_FIELDS = {
    "email_send_provider": "smtp",
    "email_send_from": "faktura@testbolaget.se",
    "email_send_smtp_host": "smtp.example.com",
    "email_send_smtp_username": "faktura@testbolaget.se",
    "email_send_smtp_password": "hemligt",
}

GRAPH_FIELDS = {
    "email_send_provider": "graph",
    "email_send_from": "faktura@testbolaget.se",
    "email_fetch_oauth_tenant_id": "tenant",
    "email_fetch_oauth_client_id": "client",
    "email_fetch_oauth_client_secret": "secret",
}


class CompanyEmailConfiguredTests(CompanyTestCase):
    user_email = "konfig@example.com"
    company_name = "Konfigbolaget AB"
    accounting_year_dates = None

    def test_unconfigured_by_default(self):
        self.assertFalse(company_email_configured(self.company))

    def test_smtp_requires_host_and_from(self):
        for field, value in SMTP_FIELDS.items():
            setattr(self.company, field, value)
        self.assertTrue(company_email_configured(self.company))
        self.company.email_send_smtp_host = ""
        self.assertFalse(company_email_configured(self.company))

    def test_graph_requires_oauth_fields_and_mailbox(self):
        for field, value in GRAPH_FIELDS.items():
            setattr(self.company, field, value)
        self.assertTrue(company_email_configured(self.company))
        self.company.email_send_from = ""
        self.assertFalse(company_email_configured(self.company))
        self.company.email_fetch_address = "import@testbolaget.se"
        self.assertTrue(company_email_configured(self.company))


class SendCompanyEmailTests(CompanyTestCase):
    user_email = "sandare@example.com"
    company_name = "Sändbolaget AB"
    accounting_year_dates = None
    company_fields = SMTP_FIELDS

    def test_smtp_success_logs_sent_row(self):
        with patch("django.core.mail.message.EmailMessage.send", return_value=1):
            result = send_company_email(
                self.company,
                purpose=SentEmail.Purpose.INVOICE,
                to=["kund@example.com"],
                subject="Faktura 1",
                body="Hej",
                attachments=[("faktura-1.pdf", "application/pdf", b"%PDF")],
                user=self.user,
            )
        self.assertEqual(result.status, SentEmail.Status.SENT)
        self.assertEqual(result.recipient, "kund@example.com")
        self.assertEqual(result.created_by, self.user)
        self.assertEqual(SentEmail.objects.filter(company=self.company).count(), 1)

    def test_smtp_failure_logs_failed_row_without_raising(self):
        with patch("django.core.mail.message.EmailMessage.send", side_effect=OSError("Connection refused")):
            result = send_company_email(
                self.company,
                purpose=SentEmail.Purpose.INVOICE,
                to=["kund@example.com"],
                subject="Faktura 1",
                body="Hej",
            )
        self.assertEqual(result.status, SentEmail.Status.FAILED)
        self.assertIn("Connection refused", result.error)

    def test_unconfigured_company_logs_failed_row(self):
        self.company.email_send_provider = Company.EmailSendProvider.NONE
        result = send_company_email(
            self.company,
            purpose=SentEmail.Purpose.INVOICE,
            to=["kund@example.com"],
            subject="Faktura 1",
            body="Hej",
        )
        self.assertEqual(result.status, SentEmail.Status.FAILED)
        self.assertIn("inte konfigurerad", result.error)

    def test_graph_path_uses_graph_client(self):
        for field, value in GRAPH_FIELDS.items():
            setattr(self.company, field, value)
        with (
            patch("bookkeeping.outgoing_mail.graph_mail.fetch_access_token", return_value="token") as fetch,
            patch("bookkeeping.outgoing_mail.graph_mail.send_mail") as graph_send,
        ):
            result = send_company_email(
                self.company,
                purpose=SentEmail.Purpose.REMINDER,
                to=["kund@example.com"],
                subject="Påminnelse",
                body="Hej",
            )
        self.assertEqual(result.status, SentEmail.Status.SENT)
        fetch.assert_called_once_with("tenant", "client", "secret")
        graph_send.assert_called_once()
        self.assertEqual(graph_send.call_args.args[1], "faktura@testbolaget.se")

    def test_graph_error_message_lands_in_log_row(self):
        for field, value in GRAPH_FIELDS.items():
            setattr(self.company, field, value)
        with patch(
            "bookkeeping.outgoing_mail.graph_mail.fetch_access_token",
            side_effect=ValueError("Microsoft avvisade Client Secret."),
        ):
            result = send_company_email(
                self.company,
                purpose=SentEmail.Purpose.INVOICE,
                to=["kund@example.com"],
                subject="Faktura 1",
                body="Hej",
            )
        self.assertEqual(result.status, SentEmail.Status.FAILED)
        self.assertIn("Client Secret", result.error)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="system@saldovibe.example",
)
class SendSystemEmailTests(CompanyTestCase):
    user_email = "system@example.com"
    company_name = "Systemmailbolaget AB"
    accounting_year_dates = None

    def _send(self):
        return send_system_email(
            self.company,
            purpose=SentEmail.Purpose.DIGEST,
            to=["anvandare@example.com"],
            subject="Digest",
            body="Hej",
        )

    def test_sends_via_global_settings_and_logs(self):
        result = self._send()
        self.assertEqual(result.status, SentEmail.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "system@saldovibe.example")
        self.assertEqual(mail.outbox[0].to, ["anvandare@example.com"])

    def test_notify_provider_outgoing_uses_company_send_account(self):
        for field, value in SMTP_FIELDS.items():
            setattr(self.company, field, value)
        self.company.email_notify_provider = Company.EmailNotifyProvider.OUTGOING
        with patch("django.core.mail.message.EmailMessage.send", return_value=1) as send:
            result = self._send()
        self.assertEqual(result.status, SentEmail.Status.SENT)
        send.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_provider_outgoing_without_send_account_logs_failure(self):
        self.company.email_notify_provider = Company.EmailNotifyProvider.OUTGOING
        result = self._send()
        self.assertEqual(result.status, SentEmail.Status.FAILED)
        self.assertIn("inte konfigurerad", result.error)
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_provider_own_smtp_account(self):
        self.company.email_notify_provider = Company.EmailNotifyProvider.SMTP
        self.company.email_notify_from = "notiser@systembolaget.se"
        self.company.email_notify_smtp_host = "smtp.notiser.example"
        with patch("django.core.mail.message.EmailMessage.send", return_value=1) as send:
            result = self._send()
        self.assertEqual(result.status, SentEmail.Status.SENT)
        send.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)

    def test_notify_provider_own_graph_mailbox(self):
        for field, value in GRAPH_FIELDS.items():
            setattr(self.company, field, value)
        self.company.email_notify_provider = Company.EmailNotifyProvider.GRAPH
        self.company.email_notify_from = "notiser@systembolaget.se"
        with (
            patch("bookkeeping.outgoing_mail.graph_mail.fetch_access_token", return_value="token"),
            patch("bookkeeping.outgoing_mail.graph_mail.send_mail") as graph_send,
        ):
            result = self._send()
        self.assertEqual(result.status, SentEmail.Status.SENT)
        self.assertEqual(graph_send.call_args.args[1], "notiser@systembolaget.se")

    def test_notify_provider_graph_falls_back_to_fetch_mailbox(self):
        for field, value in GRAPH_FIELDS.items():
            setattr(self.company, field, value)
        self.company.email_notify_provider = Company.EmailNotifyProvider.GRAPH
        self.company.email_fetch_address = "faktura@systembolaget.se"
        with (
            patch("bookkeeping.outgoing_mail.graph_mail.fetch_access_token", return_value="token"),
            patch("bookkeeping.outgoing_mail.graph_mail.send_mail") as graph_send,
        ):
            result = self._send()
        self.assertEqual(result.status, SentEmail.Status.SENT)
        self.assertEqual(graph_send.call_args.args[1], "faktura@systembolaget.se")
