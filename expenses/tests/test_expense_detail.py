import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from attachments.models import TransactionAttachment
from bookkeeping.models import AccountClass, PeriodLock
from expenses.models import ExpenseClaim
from saldovibe.testing import CompanyTestCase, create_accounts


class ExpenseClaimDetailTests(CompanyTestCase):
    user_email = "expense-detail-user@example.com"
    company_name = "Expense Detail AB"
    company_org_number = "556677-9900"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-expense-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        accounts = create_accounts(
            self.company,
            [
                ("4010", "Inköp material", AccountClass.COST_OF_GOODS),
                ("2820", "Utläggsskuld", AccountClass.EQUITY_LIABILITY),
            ],
        )
        self.expense_account = accounts["4010"]
        self.liability_account = accounts["2820"]
        self.attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt", content_type="application/pdf"),
        )

    def test_expense_detail_shows_attachment_panel(self):
        claim = ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Taxi till kund",
            expense_date=timezone.localdate(),
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            total_amount=Decimal("250.00"),
        )
        claim.attachments.add(self.attachment)

        response = self.client.get(reverse("expenses:expense_detail", args=[claim.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Taxi till kund")
        self.assertContains(response, 'id="attachmentViewerFrame"')
        self.assertContains(response, self.attachment.file_name)

    def test_attachment_can_be_added_to_a_claim_in_an_open_period(self):
        claim = ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Taxi till kund",
            expense_date="2026-06-15",
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            total_amount=Decimal("250.00"),
        )

        response = self.client.post(
            reverse("expenses:expense_attachment_add", args=[claim.pk]),
            {"selected_attachment_ids": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("expenses:expense_detail", args=[claim.pk]))
        self.assertEqual(claim.attachments.count(), 1)

    def test_attachment_cannot_be_added_or_removed_when_period_is_locked(self):
        claim = ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Taxi till kund",
            expense_date="2026-01-15",
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            total_amount=Decimal("250.00"),
        )
        claim.attachments.add(self.attachment)
        other_attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("nytt.pdf", b"%PDF-1.4 nytt", content_type="application/pdf"),
        )
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start="2026-01-01",
            period_end="2026-01-31",
            is_locked=True,
            reason="Stängd period",
            locked_by=self.user,
        )

        add_response = self.client.post(
            reverse("expenses:expense_attachment_add", args=[claim.pk]),
            {"selected_attachment_ids": str(other_attachment.pk)},
        )
        self.assertRedirects(add_response, reverse("expenses:expense_detail", args=[claim.pk]))
        self.assertEqual(claim.attachments.count(), 1)

        remove_response = self.client.post(
            reverse("expenses:expense_attachment_remove", args=[claim.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )
        self.assertRedirects(remove_response, reverse("expenses:expense_detail", args=[claim.pk]))
        self.assertEqual(claim.attachments.count(), 1)

    def test_removing_an_attachment_only_unlinks_it_from_the_claim(self):
        claim = ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Taxi till kund",
            expense_date="2026-06-15",
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            total_amount=Decimal("250.00"),
        )
        claim.attachments.add(self.attachment)

        response = self.client.post(
            reverse("expenses:expense_attachment_remove", args=[claim.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("expenses:expense_detail", args=[claim.pk]))
        self.assertEqual(claim.attachments.count(), 0)
        self.attachment.refresh_from_db()
        self.assertIsNone(self.attachment.deleted_at)

    def test_expense_detail_requires_login(self):
        claim = ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Taxi till kund",
            expense_date=timezone.localdate(),
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            total_amount=Decimal("250.00"),
        )
        self.client.logout()

        response = self.client.get(reverse("expenses:expense_detail", args=[claim.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
