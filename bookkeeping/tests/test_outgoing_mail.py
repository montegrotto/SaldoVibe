"""Central sändmodul: dispatch per provider, loggning, aldrig raise."""

from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from attachments.email_import import check_email_fetch_connection
from bookkeeping.models import Company, CompanyMembership, SentEmail
from bookkeeping.outgoing_mail import (
    company_email_configured,
    send_company_email,
    send_system_email,
    send_test_email,
)
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


class SendTestEmailTests(CompanyTestCase):
    user_email = "testare@example.com"
    company_name = "Testmejlbolaget AB"
    accounting_year_dates = None
    company_fields = SMTP_FIELDS

    def test_send_account_failure_raises_and_logs_nothing(self):
        with patch("django.core.mail.message.EmailMessage.send", side_effect=OSError("535 fel lösenord")):
            with self.assertRaisesMessage(OSError, "535 fel lösenord"):
                send_test_email(self.company, "send", ["testare@example.com"])
        self.assertFalse(SentEmail.objects.exists())

    def test_notify_own_account_uses_notify_settings(self):
        self.company.email_notify_provider = Company.EmailNotifyProvider.SMTP
        self.company.email_notify_from = "notiser@testbolaget.se"
        self.company.email_notify_smtp_host = "smtp.notiser.example"
        with patch("bookkeeping.outgoing_mail.django_mail.get_connection") as get_connection:
            with patch("django.core.mail.message.EmailMessage.send", return_value=1):
                send_test_email(self.company, "notify", ["testare@example.com"])
        self.assertEqual(get_connection.call_args.kwargs["host"], "smtp.notiser.example")
        self.assertFalse(SentEmail.objects.exists())

    @override_settings(EMAIL_HOST="")
    def test_unconfigured_system_account_is_an_error_not_a_silent_success(self):
        with self.assertRaisesMessage(ValueError, "Systemkontot är inte konfigurerat"):
            send_test_email(self.company, "notify", ["testare@example.com"])
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_HOST="smtp.system.example")
    def test_configured_system_account_sends(self):
        send_test_email(self.company, "notify", ["testare@example.com"])
        self.assertEqual(mail.outbox[0].to, ["testare@example.com"])


class CompanyEmailTestButtonTests(CompanyTestCase):
    user_email = "knapp@example.com"
    company_name = "Knappbolaget AB"
    accounting_year_dates = None

    def _post(self, account, **fields):
        return self.client.post(
            reverse("bookkeeping:company_update", args=[self.company.pk]),
            {"name": self.company.name, "org_number": "556677-8899", "test_email_account": account, **fields},
        )

    def test_saves_form_before_testing_and_returns_to_the_form(self):
        with patch("bookkeeping.views.companies.send_test_email") as send:
            response = self._post("send", email_send_smtp_password="nytt-losenord")
        self.assertRedirects(response, reverse("bookkeeping:company_update", args=[self.company.pk]))
        company = send.call_args.args[0]
        self.assertEqual(company.email_send_smtp_password, "nytt-losenord")
        self.assertEqual(send.call_args.args[1:], ("send", ["knapp@example.com"]))
        self.company.refresh_from_db()
        self.assertEqual(self.company.email_send_smtp_password, "nytt-losenord")

    def test_failure_shows_server_error_on_the_form(self):
        with patch("bookkeeping.views.companies.send_test_email", side_effect=OSError("535 fel lösenord")):
            response = self._post("notify")
        self.assertRedirects(response, reverse("bookkeeping:company_update", args=[self.company.pk]))
        shown = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("Testet misslyckades: 535 fel lösenord" in message for message in shown), shown)

    def test_fetch_runs_connection_check_and_sends_nothing(self):
        with patch("bookkeeping.views.companies.check_email_fetch_connection") as check:
            with patch("bookkeeping.views.companies.send_test_email") as send:
                self._post("fetch")
        check.assert_called_once()
        send.assert_not_called()

    def test_plain_save_runs_no_test(self):
        with patch("bookkeeping.views.companies.send_test_email") as send:
            response = self._post("")
        self.assertRedirects(response, reverse("bookkeeping:company_list"))
        send.assert_not_called()

    def test_viewer_cannot_trigger_test(self):
        self.company.memberships.filter(user=self.user).update(role=CompanyMembership.Role.VIEWER)
        with patch("bookkeeping.views.companies.send_test_email") as send:
            self._post("send")
        send.assert_not_called()


class EmailFetchConnectionCheckTests(CompanyTestCase):
    user_email = "imap@example.com"
    company_name = "Imapbolaget AB"
    accounting_year_dates = None
    company_fields = {
        "email_fetch_provider": "imap",
        "email_fetch_address": "faktura@testbolaget.se",
        "email_fetch_imap_host": "imap.example.com",
        "email_fetch_password": "hemligt",
    }

    def test_logs_in_and_opens_folder_read_only_even_when_fetch_is_disabled(self):
        self.assertFalse(self.company.email_fetch_enabled)
        with patch("attachments.email_import.imaplib.IMAP4_SSL") as imap:
            client = imap.return_value
            client.select.return_value = ("OK", [b"3"])
            check_email_fetch_connection(self.company)
        client.login.assert_called_once_with("faktura@testbolaget.se", "hemligt")
        client.select.assert_called_once_with("INBOX", readonly=True)
        client.search.assert_not_called()
        client.fetch.assert_not_called()
        client.logout.assert_called_once()

    def test_login_error_propagates_and_still_logs_out(self):
        with patch("attachments.email_import.imaplib.IMAP4_SSL") as imap:
            client = imap.return_value
            client.login.side_effect = OSError("AUTHENTICATIONFAILED")
            with self.assertRaisesMessage(OSError, "AUTHENTICATIONFAILED"):
                check_email_fetch_connection(self.company)
        client.logout.assert_called_once()

    def test_missing_folder_is_an_error(self):
        with patch("attachments.email_import.imaplib.IMAP4_SSL") as imap:
            imap.return_value.select.return_value = ("NO", [b"finns inte"])
            with self.assertRaisesMessage(ValueError, "Kunde inte öppna mappen 'INBOX'"):
                check_email_fetch_connection(self.company)

    def test_unconfigured_provider_is_an_error(self):
        self.company.email_fetch_provider = ""
        with self.assertRaisesMessage(ValueError, "e-postleverantör"):
            check_email_fetch_connection(self.company)
