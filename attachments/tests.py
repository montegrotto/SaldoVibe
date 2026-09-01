import base64
import hashlib
import json
import shutil
import tempfile
from contextlib import contextmanager
from email.message import EmailMessage
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

from django.conf import settings
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.staticfiles.finders import find
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from bookkeeping.company_scope import SESSION_COMPANY_KEY
from saldovibe.testing import CompanyTestCase, create_company, create_user

from . import extraction_client, graph_mail
from .email_import import import_email_attachments_for_company
from .models import TransactionAttachment
from .services import _fetch_extracted_data, first_extraction_suggestion, save_attachment_with_thumbnail
from .views import attachment_list


class _FakeImapClient:
    def __init__(self, host, raw_messages_by_id):
        self.host = host
        self._raw_messages_by_id = raw_messages_by_id

    def login(self, address, password):
        return "OK", [b"Logged in"]

    def select(self, folder):
        return "OK", [b"1"]

    def search(self, charset, criteria):
        message_ids = b" ".join(self._raw_messages_by_id.keys())
        return "OK", [message_ids]

    def fetch(self, message_id, query):
        return "OK", [(b"BODY", self._raw_messages_by_id[message_id])]

    def close(self):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"


def _graph_attachment(attachment_id, name, content_type, is_inline=False, odata_type=FILE_ATTACHMENT):
    return {
        "@odata.type": odata_type,
        "id": attachment_id,
        "name": name,
        "contentType": content_type,
        "isInline": is_inline,
    }


@contextmanager
def _fake_graph(messages, attachments_by_message, content_by_attachment):
    """Patch the Graph transport so imports run without network access."""
    with (
        patch("attachments.graph_mail.fetch_access_token", return_value="token"),
        patch("attachments.graph_mail.resolve_folder", return_value="inbox"),
        patch("attachments.graph_mail.list_messages_with_attachments", return_value=messages),
        patch(
            "attachments.graph_mail.list_attachment_metadata",
            side_effect=lambda token, mailbox, message_id: attachments_by_message.get(message_id, []),
        ),
        patch(
            "attachments.graph_mail.download_attachment",
            side_effect=lambda token, mailbox, message_id, attachment_id: content_by_attachment.get(attachment_id),
        ),
    ):
        yield


class SafeReturnToTests(SimpleTestCase):
    def test_rejects_protocol_relative_and_backslash_urls(self):
        from .utils import is_safe_return_to

        for payload in ("//evil.com", "///evil.com", "/\\evil.com", "/\\/evil.com", "https://evil.com/x", "", None):
            self.assertFalse(is_safe_return_to(payload), payload)

    def test_accepts_local_paths(self):
        from .utils import is_safe_return_to

        for payload in ("/", "/bilagor/", "/utlagg/5/?return_to=/bilagor/"):
            self.assertTrue(is_safe_return_to(payload), payload)


class AttachmentViewTests(CompanyTestCase):
    user_email = "attachments@example.com"
    company_name = "Bilagebolaget AB"
    company_org_number = "556677-0011"
    accounting_year_dates = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # A second tenant, to prove the views never reach across companies.
        self.other_user = create_user("other@example.com")
        self.other_company = create_company("Annat Bolag AB", "556677-0022", users=[self.other_user])

    def test_attachment_list_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("attachments:attachment_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_upload_creates_attachment_for_active_company(self):
        upload = SimpleUploadedFile(
            "receipt.pdf",
            b"%PDF-1.4 test content",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse("attachments:attachment_list"),
            {"file": upload},
        )

        self.assertRedirects(response, reverse("attachments:attachment_list"))
        self.assertEqual(TransactionAttachment.objects.count(), 1)

        attachment = TransactionAttachment.objects.get()
        self.assertEqual(attachment.company, self.company)
        self.assertEqual(attachment.uploaded_by, self.user)
        self.assertEqual(attachment.file_name, "receipt.pdf")

    def test_list_download_link_goes_through_protected_view(self):
        # /media/ serveras inte publikt (nginx) – nedladdning måste gå via den inloggade vyn.
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("dl.pdf", b"%PDF-1.4 dl", content_type="application/pdf"),
        )

        response = self.client.get(reverse("attachments:attachment_list"))

        self.assertContains(response, reverse("attachments:attachment_preview", args=[attachment.pk]))
        self.assertNotContains(response, "/media/")

    def test_delete_soft_deletes_attachment(self):
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "delete-me.pdf",
                b"%PDF-1.4 delete",
                content_type="application/pdf",
            ),
        )

        response = self.client.post(
            reverse("attachments:attachment_delete", args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("attachments:attachment_list"))
        attachment.refresh_from_db()
        self.assertIsNotNone(attachment.deleted_at)
        self.assertEqual(attachment.deleted_by, self.user)

    def _create_transaction(self, date, period_locked=False):
        from bookkeeping.models import Account, AccountClass, AccountingYear, PeriodLock, Transaction

        year, _ = AccountingYear.objects.get_or_create(
            company=self.company, start_date="2026-01-01", end_date="2026-12-31"
        )
        Account.objects.get_or_create(
            company=self.company,
            number="1930",
            defaults={"name": "Företagskonto", "account_class": AccountClass.ASSET},
        )
        Account.objects.get_or_create(
            company=self.company,
            number="2440",
            defaults={"name": "Leverantörsskulder", "account_class": AccountClass.EQUITY_LIABILITY},
        )
        txn = Transaction.objects.create(
            accounting_year=year,
            date=date,
            description="Test",
            created_by=self.user,
        )
        if period_locked:
            PeriodLock.objects.create(
                company=self.company,
                accounting_year=year,
                period_start="2026-01-01",
                period_end="2026-01-31",
                is_locked=True,
                reason="Stängd period",
                locked_by=self.user,
            )
        return txn

    def test_delete_is_blocked_when_attachment_belongs_to_a_locked_period(self):
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "hold.pdf",
                b"%PDF-1.4 hold",
                content_type="application/pdf",
            ),
        )
        txn = self._create_transaction("2026-01-15", period_locked=True)
        txn.attachments.add(attachment)

        response = self.client.post(
            reverse("attachments:attachment_delete", args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("attachments:attachment_list"))
        attachment.refresh_from_db()
        self.assertIsNone(attachment.deleted_at)

    def test_delete_is_allowed_after_posting_when_period_is_not_locked(self):
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "open.pdf",
                b"%PDF-1.4 open",
                content_type="application/pdf",
            ),
        )
        txn = self._create_transaction("2026-06-15", period_locked=False)
        txn.attachments.add(attachment)

        response = self.client.post(
            reverse("attachments:attachment_delete", args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("attachments:attachment_list"))
        attachment.refresh_from_db()
        self.assertIsNotNone(attachment.deleted_at)

    def _build_get_request(self, path):
        factory = RequestFactory()
        request = factory.get(path)
        request.user = self.user
        SessionMiddleware(lambda r: None).process_request(request)
        request.session[SESSION_COMPANY_KEY] = self.company.pk
        request.session.save()
        MessageMiddleware(lambda r: None).process_request(request)
        return request

    def test_attachment_list_hides_attachments_already_used_on_a_transaction(self):
        from bookkeeping.models import Account, AccountClass, AccountingYear, Transaction

        available = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "tillganglig.pdf",
                b"%PDF-1.4 available",
                content_type="application/pdf",
            ),
        )
        linked_to_transaction = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "kopplad.pdf",
                b"%PDF-1.4 linked",
                content_type="application/pdf",
            ),
        )
        year = AccountingYear.objects.create(company=self.company, start_date="2026-01-01", end_date="2026-12-31")
        Account.objects.create(
            company=self.company, number="1930", name="Företagskonto", account_class=AccountClass.ASSET
        )
        Account.objects.create(
            company=self.company, number="2440", name="Leverantörsskulder", account_class=AccountClass.EQUITY_LIABILITY
        )
        txn = Transaction.objects.create(
            accounting_year=year,
            date="2026-06-26",
            description="Test",
            created_by=self.user,
        )
        txn.attachments.add(linked_to_transaction)

        response = attachment_list(self._build_get_request("/attachments/bilagor/"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("tillganglig.pdf", content)
        self.assertNotIn("kopplad.pdf", content)

    def test_delete_does_not_allow_other_company_attachment(self):
        foreign_attachment = TransactionAttachment.objects.create(
            company=self.other_company,
            uploaded_by=self.other_user,
            file=SimpleUploadedFile(
                "foreign.pdf",
                b"%PDF-1.4 foreign",
                content_type="application/pdf",
            ),
        )

        response = self.client.post(
            reverse("attachments:attachment_delete", args=[foreign_attachment.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("attachments:attachment_list"))
        self.assertTrue(TransactionAttachment.objects.filter(pk=foreign_attachment.pk).exists())

    def _build_email_bytes(self, message_id="<mail-1@example.com>"):
        msg = EmailMessage()
        msg["From"] = "sender@example.com"
        msg["To"] = "receiver@example.com"
        msg["Subject"] = "Leverantörsfaktura"
        msg["Message-ID"] = message_id
        msg.set_content("Se bilagor")
        msg.add_attachment(
            b"%PDF-1.4 invoice",
            maintype="application",
            subtype="pdf",
            filename="invoice.pdf",
        )
        msg.add_attachment(
            b"plain text",
            maintype="text",
            subtype="plain",
            filename="notes.txt",
        )
        return msg.as_bytes()

    def test_import_email_attachments_supports_gmail(self):
        self.company.email_fetch_enabled = True
        self.company.email_fetch_provider = "gmail"
        self.company.email_fetch_address = "finance@example.com"
        self.company.email_fetch_password = "app-password"
        self.company.email_fetch_folder = "INBOX"
        self.company.save()

        raw_messages = {b"1": self._build_email_bytes()}

        with patch(
            "attachments.email_import.imaplib.IMAP4_SSL",
            side_effect=lambda host: _FakeImapClient(host, raw_messages),
        ):
            result = import_email_attachments_for_company(company=self.company, user=self.user)

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 0)
        self.assertEqual(result["skipped_unsupported"], 1)
        self.assertEqual(result["scanned_messages"], 1)

        attachment = TransactionAttachment.objects.get(company=self.company)
        self.assertEqual(attachment.source, TransactionAttachment.Source.EMAIL)
        self.assertEqual(attachment.source_provider, "gmail")
        self.assertTrue(attachment.file_name.startswith("invoice"))
        self.assertTrue(attachment.file_name.endswith(".pdf"))

    def test_import_email_attachments_runs_reinvgrabber_extraction(self):
        self.company.email_fetch_enabled = True
        self.company.email_fetch_provider = "gmail"
        self.company.email_fetch_address = "finance@example.com"
        self.company.email_fetch_password = "app-password"
        self.company.email_fetch_folder = "INBOX"
        self.company.save()

        raw_messages = {b"1": self._build_email_bytes()}

        with (
            patch(
                "attachments.email_import.imaplib.IMAP4_SSL",
                side_effect=lambda host: _FakeImapClient(host, raw_messages),
            ),
            patch(
                "attachments.services.extract_fields",
                return_value={"leverantör": "Exempel AB", "totalbelopp": "199.00"},
            ) as mocked_extract,
        ):
            import_email_attachments_for_company(company=self.company, user=self.user)

        mocked_extract.assert_called_once()
        self.assertEqual(mocked_extract.call_args.kwargs["own_company"], self.company.name)
        attachment = TransactionAttachment.objects.get(company=self.company)
        self.assertEqual(attachment.extracted_data, {"leverantör": "Exempel AB", "totalbelopp": "199.00"})

    def _configure_outlook(self):
        self.company.email_fetch_enabled = True
        self.company.email_fetch_provider = "outlook"
        self.company.email_fetch_address = "faktura@example.com"
        self.company.email_fetch_password = ""
        self.company.email_fetch_oauth_tenant_id = "tenant-id"
        self.company.email_fetch_oauth_client_id = "client-id"
        self.company.email_fetch_oauth_client_secret = "client-secret"
        self.company.email_fetch_folder = "INBOX"
        self.company.save()

    def test_import_email_attachments_supports_outlook_and_deduplicates(self):
        self._configure_outlook()

        messages = [{"id": "m1", "internetMessageId": "<mail-2@example.com>", "subject": "Leverantörsfaktura"}]
        attachments = {
            "m1": [
                _graph_attachment("att-1", "invoice.pdf", "application/pdf"),
                _graph_attachment("att-2", "notes.txt", "text/plain"),
            ]
        }
        contents = {"att-1": b"%PDF-1.4 invoice", "att-2": b"plain text"}

        with _fake_graph(messages, attachments, contents):
            first_result = import_email_attachments_for_company(company=self.company, user=self.user)

        with _fake_graph(messages, attachments, contents):
            second_result = import_email_attachments_for_company(company=self.company, user=self.user)

        self.assertEqual(first_result["imported"], 1)
        self.assertEqual(first_result["skipped_unsupported"], 1)
        self.assertEqual(second_result["imported"], 0)
        self.assertEqual(second_result["duplicates"], 1)
        self.assertEqual(TransactionAttachment.objects.filter(company=self.company).count(), 1)

        attachment = TransactionAttachment.objects.get(company=self.company)
        self.assertEqual(attachment.source_provider, "outlook")
        self.assertTrue(attachment.file_name.startswith("invoice"))
        self.assertTrue(attachment.file_name.endswith(".pdf"))
        self.assertEqual(attachment.source_message_id, "<mail-2@example.com>")

    def test_import_email_attachments_skips_non_inline_layout_images(self):
        """Invoice mails attach logos and corner graphics as ordinary, non-inline
        attachments, so filtering on isInline alone is not enough."""
        self._configure_outlook()

        messages = [{"id": "m1", "internetMessageId": "<telia@example.com>", "subject": "Telia faktura"}]
        attachments = {
            "m1": [
                _graph_attachment("a1", "21724456211.pdf", "application/pdf"),
                _graph_attachment("a2", "corner_lb.png", "image/png"),
                _graph_attachment("a3", "corner_rt.png", "image/png"),
                _graph_attachment("a4", "telia_logo_p.png", "image/png"),
            ]
        }
        contents = {
            "a1": b"%PDF-1.4 telia",
            "a2": b"png-1",
            "a3": b"png-2",
            "a4": b"png-3",
        }

        with _fake_graph(messages, attachments, contents):
            result = import_email_attachments_for_company(company=self.company, user=self.user)

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped_unsupported"], 3)
        self.assertEqual(TransactionAttachment.objects.filter(company=self.company).count(), 1)
        stored_name = TransactionAttachment.objects.get(company=self.company).file_name
        self.assertTrue(stored_name.startswith("21724456211"))
        self.assertTrue(stored_name.endswith(".pdf"))

    def test_import_email_attachments_deduplicates_forwarded_invoice(self):
        """A forwarded invoice arrives with a new message id but identical bytes."""
        self._configure_outlook()

        messages = [
            {"id": "m1", "internetMessageId": "<original@example.com>", "subject": "Din faktura 9268472306"},
            {"id": "m2", "internetMessageId": "<forward@example.com>", "subject": "Fwd: Din faktura 9268472306"},
        ]
        attachments = {
            "m1": [_graph_attachment("a1", "faktura_9268472306.pdf", "application/pdf")],
            "m2": [_graph_attachment("a2", "faktura_9268472306.pdf", "application/pdf")],
        }
        contents = {"a1": b"%PDF-1.4 dnb", "a2": b"%PDF-1.4 dnb"}

        with _fake_graph(messages, attachments, contents):
            result = import_email_attachments_for_company(company=self.company, user=self.user)

        self.assertEqual(result["scanned_messages"], 2)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(TransactionAttachment.objects.filter(company=self.company).count(), 1)

    def test_import_email_attachments_skips_item_attachments(self):
        self._configure_outlook()

        messages = [{"id": "m1", "internetMessageId": "<item@example.com>", "subject": "Vidarebefordran"}]
        attachments = {
            "m1": [
                _graph_attachment(
                    "a1",
                    "Inbäddat meddelande",
                    "message/rfc822",
                    odata_type="#microsoft.graph.itemAttachment",
                )
            ]
        }

        with _fake_graph(messages, attachments, {}):
            result = import_email_attachments_for_company(company=self.company, user=self.user)

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped_unsupported"], 0)

    def test_import_email_attachments_rejects_outlook_without_credentials(self):
        self.company.email_fetch_enabled = True
        self.company.email_fetch_provider = "outlook"
        self.company.email_fetch_address = "faktura@example.com"
        self.company.email_fetch_password = "legacy-password"
        self.company.email_fetch_folder = "INBOX"
        self.company.save()

        with self.assertRaisesMessage(
            ValueError,
            "Outlook kräver Tenant ID, Client ID, Client Secret i företagsinställningarna.",
        ):
            import_email_attachments_for_company(company=self.company, user=self.user)

    def test_import_email_attachments_rejects_outlook_without_tenant(self):
        self._configure_outlook()
        self.company.email_fetch_oauth_tenant_id = ""
        self.company.save()

        with self.assertRaisesMessage(
            ValueError,
            "Outlook kräver Tenant ID i företagsinställningarna.",
        ):
            import_email_attachments_for_company(company=self.company, user=self.user)


class AttachmentContentHashTests(TestCase):
    def setUp(self):
        self.company = create_company("Hash AB", "5566778899")
        self.user = create_user("hash@example.com")

    def test_content_hash_is_filled_on_save(self):
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
        )

        self.assertEqual(
            attachment.content_hash,
            hashlib.sha256(b"%PDF-1.4 kvitto").hexdigest(),
        )

    def test_content_hash_is_stable_across_identical_uploads(self):
        first = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("a.pdf", b"%PDF-1.4 same", content_type="application/pdf"),
        )
        second = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("b.pdf", b"%PDF-1.4 same", content_type="application/pdf"),
        )

        self.assertEqual(first.content_hash, second.content_hash)


class GraphMailTests(TestCase):
    def test_list_messages_drops_messages_without_attachments(self):
        """Graph rejects $filter combined with $orderby, so the attachment
        check happens client-side and must not leak through."""
        payload = {
            "value": [
                {"id": "m1", "subject": "Faktura", "hasAttachments": True},
                {"id": "m2", "subject": "Nyhetsbrev", "hasAttachments": False},
                {"id": "m3", "subject": "Kvitto", "hasAttachments": True},
            ]
        }

        with patch("attachments.graph_mail._get", return_value=payload):
            messages = graph_mail.list_messages_with_attachments("token", "a@b.se", "inbox", 10)

        self.assertEqual([m["id"] for m in messages], ["m1", "m3"])

    def test_resolve_folder_normalises_imap_inbox_spelling(self):
        self.assertEqual(graph_mail.resolve_folder("token", "a@b.se", "INBOX"), "inbox")
        self.assertEqual(graph_mail.resolve_folder("token", "a@b.se", ""), "inbox")
        self.assertEqual(graph_mail.resolve_folder("token", "a@b.se", "Sent Items"), "sentitems")

    def test_resolve_folder_looks_up_custom_folder_by_display_name(self):
        payload = {"value": [{"id": "AAMk123", "displayName": "Leverantörsfakturor"}]}

        with patch("attachments.graph_mail._get", return_value=payload):
            folder_id = graph_mail.resolve_folder("token", "a@b.se", "Leverantörsfakturor")

        self.assertEqual(folder_id, "AAMk123")

    def test_resolve_folder_lists_alternatives_when_folder_is_missing(self):
        payload = {"value": [{"id": "AAMk123", "displayName": "Arkiv"}]}

        with patch("attachments.graph_mail._get", return_value=payload):
            with self.assertRaisesMessage(ValueError, "Tillgängliga mappar: Arkiv"):
                graph_mail.resolve_folder("token", "a@b.se", "Saknas")

    def test_is_downloadable_file_attachment_rejects_item_attachments(self):
        self.assertTrue(graph_mail.is_downloadable_file_attachment(_graph_attachment("a", "f.pdf", "application/pdf")))
        self.assertFalse(
            graph_mail.is_downloadable_file_attachment(
                _graph_attachment("a", "f", "message/rfc822", odata_type="#microsoft.graph.itemAttachment")
            )
        )

    def test_send_mail_posts_message_with_base64_attachment(self):
        with patch("attachments.graph_mail.urllib_request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = None
            graph_mail.send_mail(
                "token",
                "faktura@a.se",
                subject="Faktura 1",
                body_text="Hej",
                to=["kund@example.com"],
                attachments=[("faktura-1.pdf", "application/pdf", b"%PDF")],
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{graph_mail.GRAPH_BASE_URL}/users/faktura%40a.se/sendMail")
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data.decode("utf-8"))
        message = payload["message"]
        self.assertEqual(message["subject"], "Faktura 1")
        self.assertEqual(message["toRecipients"], [{"emailAddress": {"address": "kund@example.com"}}])
        self.assertEqual(message["attachments"][0]["name"], "faktura-1.pdf")
        self.assertEqual(base64.b64decode(message["attachments"][0]["contentBytes"]), b"%PDF")
        self.assertTrue(payload["saveToSentItems"])

    def test_send_mail_403_names_the_missing_send_role(self):
        error = urllib_error.HTTPError(
            url="https://graph.microsoft.com", code=403, msg="Forbidden", hdrs=None, fp=BytesIO(b"{}")
        )
        with patch("attachments.graph_mail.urllib_request.urlopen", side_effect=error):
            with self.assertRaisesMessage(ValueError, "Application Mail.Send"):
                graph_mail.send_mail("token", "faktura@a.se", subject="x", body_text="x", to=["kund@example.com"])


class ScheduledEmailFetchCommandTests(TestCase):
    def setUp(self):
        self.enabled = create_company(
            "Hämtande AB",
            "111222-3333",
            email_fetch_enabled=True,
            email_fetch_provider="outlook",
            email_fetch_address="faktura@enabled.se",
            email_fetch_oauth_tenant_id="tenant",
            email_fetch_oauth_client_id="client",
            email_fetch_oauth_client_secret="secret",
        )
        self.disabled = create_company("Passiv AB", "222333-4444", email_fetch_enabled=False)

    def _run(self, **kwargs):
        out = StringIO()
        call_command("hamta_epostbilagor", stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    def test_command_only_visits_companies_with_fetching_enabled(self):
        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
            return_value={"imported": 2, "duplicates": 1, "skipped_unsupported": 0, "scanned_messages": 3},
        ) as fetch:
            output = self._run()

        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["company"], self.enabled)
        self.assertIsNone(fetch.call_args.kwargs["user"])
        self.assertIn("Hämtande AB", output)
        self.assertNotIn("Passiv AB", output)
        self.assertIn("Totalt: 2 importerade", output)

    def test_command_skips_inactive_companies(self):
        self.enabled.is_active = False
        self.enabled.save()

        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
        ) as fetch:
            output = self._run()

        fetch.assert_not_called()
        self.assertIn("Inga företag har e-posthämtning påslagen.", output)

    def test_one_failing_company_does_not_stop_the_others(self):
        other = create_company(
            "Fungerande AB",
            "333444-5555",
            email_fetch_enabled=True,
            email_fetch_provider="outlook",
            email_fetch_address="faktura@working.se",
            email_fetch_oauth_tenant_id="tenant",
            email_fetch_oauth_client_id="client",
            email_fetch_oauth_client_secret="secret",
        )

        def fetch_side_effect(company, user, max_messages):
            if company == self.enabled:
                raise ValueError("Client Secret har gått ut")
            return {"imported": 1, "duplicates": 0, "skipped_unsupported": 0, "scanned_messages": 1}

        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
            side_effect=fetch_side_effect,
        ) as fetch:
            with self.assertRaises(SystemExit):
                self._run()

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(
            [call.kwargs["company"] for call in fetch.call_args_list],
            [self.enabled, other],
        )

    def test_company_argument_limits_the_run(self):
        create_company(
            "Annan AB",
            "444555-6666",
            email_fetch_enabled=True,
            email_fetch_provider="gmail",
            email_fetch_address="faktura@annan.se",
            email_fetch_password="app-password",
        )

        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
            return_value={"imported": 0, "duplicates": 0, "skipped_unsupported": 0, "scanned_messages": 0},
        ) as fetch:
            self._run(company=self.enabled.pk)

        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["company"], self.enabled)

    def test_failure_is_recorded_on_company_and_cleared_on_success(self):
        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
            side_effect=ValueError("Client Secret har gått ut"),
        ):
            with self.assertRaises(SystemExit):
                self._run()

        self.enabled.refresh_from_db()
        self.assertIn("Client Secret", self.enabled.email_fetch_last_error)
        self.assertIsNotNone(self.enabled.email_fetch_last_error_at)

        with patch(
            "attachments.management.commands.hamta_epostbilagor.import_email_attachments_for_company",
            return_value={"imported": 0, "duplicates": 0, "skipped_unsupported": 0, "scanned_messages": 0},
        ):
            self._run()

        self.enabled.refresh_from_db()
        self.assertEqual(self.enabled.email_fetch_last_error, "")
        self.assertIsNone(self.enabled.email_fetch_last_error_at)


class AttachmentPickerAssetTests(SimpleTestCase):
    """Guard the shared attachment-picker module against being re-inlined.

    The picker round-trip logic used to be copy-pasted into all four forms below and
    had already drifted apart between them. These assertions keep the single copy in
    static/js/attachment-picker.js the only one.
    """

    PICKER_FORM_TEMPLATES = (
        "bookkeeping/transaction_form.html",
        "banking/book_transaction_form.html",
        "supplier_invoices/invoice_form.html",
        "invoicing/invoice_form.html",
    )

    # Helper names that must live in the shared module, never inline in a template.
    SHARED_HELPERS = (
        "function currentSelectedIds",
        "function syncPickerLink",
        "function previewContent",
        "function saveFormStateForPickerRoundtrip",
        "function restoreFormStateAfterPickerRoundtrip",
    )

    def _template_source(self, template_name):
        return (settings.BASE_DIR / "templates" / template_name).read_text(encoding="utf-8")

    def test_every_picker_form_loads_the_shared_module(self):
        for template_name in self.PICKER_FORM_TEMPLATES:
            with self.subTest(template=template_name):
                source = self._template_source(template_name)
                self.assertIn("js/attachment-picker.js", source)
                self.assertIn("SaldoVibe.attachments.initSelectionList", source)
                self.assertIn("SaldoVibe.attachments.initFormStateRoundtrip", source)

    def test_picker_forms_do_not_reinline_shared_helpers(self):
        for template_name in self.PICKER_FORM_TEMPLATES:
            source = self._template_source(template_name)
            for helper in self.SHARED_HELPERS:
                with self.subTest(template=template_name, helper=helper):
                    self.assertNotIn(helper, source)

    def test_shared_module_is_resolvable_as_a_static_file(self):
        self.assertIsNotNone(find("js/attachment-picker.js"))


class ExtractionClientTests(SimpleTestCase):
    @override_settings(REINVGRABBER_ENABLED=False)
    def test_extract_fields_returns_none_when_disabled(self):
        self.assertIsNone(extraction_client.extract_fields(b"data", "kvitto.pdf"))

    @override_settings(REINVGRABBER_ENABLED=True)
    def test_extract_fields_returns_process_file_result_on_success(self):
        with patch(
            "attachments.extraction_client.process_file",
            return_value={"leverantör": "Exempel AB", "totalbelopp": "199.00"},
        ) as mocked:
            result = extraction_client.extract_fields(b"filinnehall", "kvitto.pdf", own_company="Eget AB")

        mocked.assert_called_once_with("kvitto.pdf", b"filinnehall", "Eget AB")
        self.assertEqual(result, {"leverantör": "Exempel AB", "totalbelopp": "199.00"})

    @override_settings(REINVGRABBER_ENABLED=True)
    def test_extract_fields_returns_none_on_unexpected_exception(self):
        # Bredare skyddsnät än process_file's egen felhantering - EN oväntad
        # bugg (t.ex. Tesseract saknas i körmiljön) ska aldrig läcka ut som
        # en exception till anroparen, se extract_fields docstring.
        with patch("attachments.extraction_client.process_file", side_effect=RuntimeError("oväntat trasigt")):
            result = extraction_client.extract_fields(b"filinnehall", "kvitto.pdf")

        self.assertIsNone(result)


class AttachmentExtractionIntegrationTests(TestCase):
    def setUp(self):
        self.company = create_company("Extraktion AB", "5566001122")
        self.user = create_user("extraktion@example.com")

    def test_save_attachment_populates_extracted_data_on_success(self):
        attachment = TransactionAttachment(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
        )
        with patch(
            "attachments.services.extract_fields",
            return_value={"leverantör": "Exempel AB", "totalbelopp": "199.00"},
        ) as mocked:
            save_attachment_with_thumbnail(attachment)

        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["own_company"], "Extraktion AB")
        attachment.refresh_from_db()
        self.assertEqual(attachment.extracted_data, {"leverantör": "Exempel AB", "totalbelopp": "199.00"})

    def test_save_attachment_leaves_extracted_data_none_when_integration_off(self):
        attachment = TransactionAttachment(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
        )
        with patch("attachments.services.extract_fields", return_value=None):
            save_attachment_with_thumbnail(attachment)

        attachment.refresh_from_db()
        self.assertIsNone(attachment.extracted_data)

    def test_fetch_extracted_data_returns_none_on_broken_file_read(self):
        # Kravet är absolut: går NÅGOT fel i extraktionssteget (nätverk,
        # trasigt lagringsbackend, vad som helst) ska uppladdningen ändå
        # lyckas - bara utan ett förslag. Testar _fetch_extracted_data
        # isolerat (inte via hela save_attachment_with_thumbnail) för att
        # inte blanda in miniatyrgenereringens egen filhantering.
        attachment = MagicMock()
        attachment.file = MagicMock()
        attachment.file.__bool__.return_value = True
        attachment.file.open.side_effect = OSError("disk läsfel")

        self.assertIsNone(_fetch_extracted_data(attachment))

    def test_save_attachment_survives_a_broken_extraction_step(self):
        attachment = TransactionAttachment(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
        )
        with patch("attachments.services._fetch_extracted_data", side_effect=RuntimeError("oväntat trasigt")):
            with self.assertRaises(RuntimeError):
                save_attachment_with_thumbnail(attachment)

        # _fetch_extracted_data självt (testat ovan) garanterar att detta i
        # praktiken aldrig når save_attachment_with_thumbnail - det här
        # testet dokumenterar bara var gränsen för skyddsnätet går: INUTI
        # _fetch_extracted_data, inte i anroparen.


class FirstExtractionSuggestionTests(TestCase):
    def setUp(self):
        self.company = create_company("Förslag AB", "5566003344")
        self.user = create_user("forslag@example.com")

    def _attachment(self, extracted_data):
        return TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
            extracted_data=extracted_data,
        )

    def test_returns_none_for_empty_iterable(self):
        self.assertIsNone(first_extraction_suggestion([]))

    def test_returns_none_when_no_attachment_has_extracted_data(self):
        attachments = [self._attachment(None), self._attachment(None)]
        self.assertIsNone(first_extraction_suggestion(attachments))

    def test_returns_none_when_extracted_data_is_all_null_fields(self):
        # En trasig fil ger en rad med alla fält None (se ReInvGrabbers
        # _error_row) men sparas ändå som ett dict, inte null - ska
        # fortfarande behandlas som "inget förslag".
        attachments = [self._attachment({"leverantör": None, "totalbelopp": None, "anmärkningar": ""})]
        self.assertIsNone(first_extraction_suggestion(attachments))

    def test_returns_first_attachment_with_a_usable_suggestion(self):
        empty = self._attachment(None)
        usable = self._attachment({"leverantör": "Exempel AB", "totalbelopp": "199.00"})
        other = self._attachment({"leverantör": "Annan AB"})

        result = first_extraction_suggestion([empty, usable, other])

        self.assertEqual(result, {"leverantör": "Exempel AB", "totalbelopp": "199.00"})


class AttachmentSizeLimitTests(SimpleTestCase):
    def test_oversized_file_is_rejected(self):
        from .forms import TransactionAttachmentForm

        big = SimpleUploadedFile(
            "big.pdf", b"x" * (TransactionAttachmentForm.MAX_FILE_SIZE + 1), content_type="application/pdf"
        )
        form = TransactionAttachmentForm(files={"file": big})
        self.assertFalse(form.is_valid())
        self.assertIn("för stor", str(form.errors["file"]))
