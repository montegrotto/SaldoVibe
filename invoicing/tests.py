import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from attachments.models import TransactionAttachment
from bookkeeping.models import AccountClass, SentEmail
from saldovibe.testing import CompanyTestCase, create_accounting_year, create_accounts

from .models import (
    Article,
    Customer,
    Invoice,
    InvoiceLine,
    RecurringInvoice,
    RecurringInvoiceInterval,
)


class InvoicingWorkflowTests(CompanyTestCase):
    user_email = "invoice-app-user@example.com"
    company_name = "Invoice App AB"
    company_org_number = "556600-1122"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-invoicing-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # The invoicing code reads this one by its own name.
        self.accounting_year = self.year

        self.customer = Customer.objects.create(
            company=self.company,
            name="Kundbolaget AB",
            org_number="556611-2233",
            email="kund@example.com",
            is_active=True,
        )

        accounts = create_accounts(
            self.company,
            [
                ("1510", "Kundfordringar", AccountClass.ASSET),
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("3740", "Öres- och kronutjämning", AccountClass.REVENUE),
                ("2611", "Utgående moms 25%", AccountClass.EQUITY_LIABILITY),
                ("3041", "Försäljning tjänster 25%", AccountClass.REVENUE),
            ],
        )
        self.receivable_account = accounts["1510"]
        self.bank_account = accounts["1930"]
        self.rounding_account = accounts["3740"]
        self.output_vat_account = accounts["2611"]
        self.income_account = accounts["3041"]

        self.article = Article.objects.create(
            company=self.company,
            article_number="A100",
            name="Konsulttimme",
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.income_account,
            is_active=True,
        )
        self.attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "kvitto.pdf",
                b"%PDF-1.4 kvitto",
                content_type="application/pdf",
            ),
        )

    def test_mixed_vat_invoice_total_matches_the_booked_receivable(self):
        # Per-line rounding: what the customer is asked to pay must equal what
        # lands on 1510, or the reskontra drifts by an öre per invoice.
        create_accounts(self.company, [("2621", "Utgående moms 12%", AccountClass.EQUITY_LIABILITY)])
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date=date(2026, 6, 1),
            due_date=date(2026, 7, 1),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Rad 25%",
            quantity=Decimal("1.38"),
            unit_price=Decimal("746.07"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Rad 12%",
            quantity=Decimal("2.62"),
            unit_price=Decimal("154.56"),
            vat_rate=Decimal("12.00"),
            sort_order=1,
        )

        invoice.bookkeep(self.user)

        booked_receivable = invoice.booked_transaction.entries.get(account=self.receivable_account).debit
        self.assertEqual(invoice.total_amount, booked_receivable)

    def test_selected_attachment_is_linked_when_invoice_is_booked(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Anna",
                "notes": "Bokförd direkt",
                "book": "1",
                "selected_attachment_ids": str(self.attachment.pk),
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("invoicing:invoice_list"))

        invoice = Invoice.objects.get(company=self.company)
        self.assertTrue(invoice.is_booked)
        self.assertEqual(invoice.attachments.count(), 1)

    def test_selected_attachment_is_linked_for_draft_invoice(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Anna",
                "notes": "Utkast",
                "selected_attachment_ids": str(self.attachment.pk),
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(company=self.company)
        self.assertFalse(invoice.is_booked)
        self.assertEqual(invoice.attachments.count(), 1)

    def test_attachment_can_be_added_to_a_booked_invoice_in_an_open_period(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date=date(2026, 6, 26),
            due_date=date(2026, 7, 26),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.bookkeep(self.user)

        response = self.client.post(
            reverse("invoicing:invoice_attachment_add", args=[invoice.pk]),
            {"selected_attachment_ids": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

    def test_attachment_cannot_be_added_or_removed_when_period_is_locked(self):
        from bookkeeping.models import PeriodLock

        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date=date(2026, 1, 15),
            due_date=date(2026, 2, 15),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.attachments.add(self.attachment)
        invoice.bookkeep(self.user)
        other_attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("nytt.pdf", b"%PDF-1.4 nytt", content_type="application/pdf"),
        )
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.accounting_year,
            period_start="2026-01-01",
            period_end="2026-01-31",
            is_locked=True,
            reason="Stängd period",
            locked_by=self.user,
        )

        add_response = self.client.post(
            reverse("invoicing:invoice_attachment_add", args=[invoice.pk]),
            {"selected_attachment_ids": str(other_attachment.pk)},
        )
        self.assertRedirects(add_response, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

        remove_response = self.client.post(
            reverse("invoicing:invoice_attachment_remove", args=[invoice.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )
        self.assertRedirects(remove_response, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

    def test_removing_an_attachment_only_unlinks_it_from_the_invoice(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date=date(2026, 6, 26),
            due_date=date(2026, 7, 26),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.attachments.add(self.attachment)
        invoice.bookkeep(self.user)

        response = self.client.post(
            reverse("invoicing:invoice_attachment_remove", args=[invoice.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 0)
        self.attachment.refresh_from_db()
        self.assertIsNone(self.attachment.deleted_at)

    def test_can_create_invoice_with_lines_and_generate_number(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Anna",
                "notes": "Tack for ert fortroende",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)
        self.assertTrue(invoice.invoice_number.startswith("2026-"))
        self.assertEqual(invoice.ocr_code, "202600011")
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.subtotal_ex_vat, Decimal("2000.00"))
        self.assertEqual(invoice.vat_amount, Decimal("500.0000"))
        self.assertFalse(invoice.is_booked)

    def test_can_create_and_book_invoice(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Anna",
                "notes": "Bokförd direkt",
                "book": "1",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)
        self.assertTrue(invoice.is_booked)
        self.assertIsNotNone(invoice.booked_transaction)
        self.assertEqual(invoice.receivable_account_id, self.receivable_account.pk)

        entries = invoice.booked_transaction.entries.select_related("account")
        self.assertEqual(entries.count(), 3)
        self.assertTrue(entries.filter(account__number="1510", debit=Decimal("2500.00")).exists())
        self.assertTrue(entries.filter(account__number="3041", credit=Decimal("2000.00")).exists())
        self.assertTrue(entries.filter(account__number="2611", credit=Decimal("500.00")).exists())

    def test_invoice_registry_shows_ocr(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )

        response = self.client.get(reverse("invoicing:invoice_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, invoice.ocr_code)
        self.assertContains(response, reverse("invoicing:invoice_create"))
        self.assertContains(response, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertNotContains(response, "Skapas automatiskt från fakturanummer")

    def test_can_book_draft_invoice_from_detail_action(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )

        response = self.client.post(reverse("invoicing:invoice_book", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("invoicing:invoice_detail", args=[invoice.pk]))

        invoice.refresh_from_db()
        self.assertTrue(invoice.is_booked)
        self.assertIsNotNone(invoice.booked_transaction)

    def test_can_delete_draft_invoice_from_detail_action(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )

        response = self.client.post(reverse("invoicing:invoice_delete", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("invoicing:invoice_list"))
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_cannot_delete_booked_invoice(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.bookkeep(self.user)

        response = self.client.post(reverse("invoicing:invoice_delete", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_invoice_save_overrides_manual_ocr_with_generated_value(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            ocr_code="9999999999",
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )

        self.assertEqual(invoice.invoice_number, "2026-0001")
        self.assertEqual(invoice.ocr_code, "202600011")

    def test_invoice_create_shows_selected_attachment_in_viewer_panel(self):
        response = self.client.get(f"{reverse('invoicing:invoice_create')}?selected_attachments={self.attachment.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="attachmentViewerFrame"')
        self.assertContains(response, self.attachment.file_name)

    def test_invoice_create_page_is_separate_from_invoice_list(self):
        response = self.client.get(reverse("invoicing:invoice_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Till fakturalista")
        self.assertContains(response, "Skapas automatiskt från fakturanummer")
        self.assertNotContains(response, "Inga kundfakturor ännu.")

    def test_customer_list_and_edit_have_new_invoice_shortcuts(self):
        list_response = self.client.get(reverse("invoicing:customer_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, f"{reverse('invoicing:invoice_create')}?customer={self.customer.pk}")
        self.assertContains(list_response, "Ny faktura")

        edit_response = self.client.get(reverse("invoicing:customer_update", args=[self.customer.pk]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "Ny faktura för kunden")
        self.assertContains(edit_response, f"{reverse('invoicing:invoice_create')}?customer={self.customer.pk}")

    def test_can_create_credit_invoice_from_booked_invoice(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.bookkeep(self.user)

        response = self.client.post(reverse("invoicing:invoice_credit", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)

        credit_invoice = Invoice.objects.exclude(pk=invoice.pk).get(company=self.company)
        self.assertTrue(credit_invoice.is_credit_invoice)
        self.assertTrue(credit_invoice.is_booked)
        self.assertEqual(credit_invoice.total_amount, Decimal("-1250.0000"))
        entries = credit_invoice.booked_transaction.entries.select_related("account")
        self.assertTrue(entries.filter(account__number="1510", credit=Decimal("1250.00")).exists())
        self.assertTrue(entries.filter(account__number="3041", debit=Decimal("1000.00")).exists())
        self.assertTrue(entries.filter(account__number="2611", debit=Decimal("250.00")).exists())

    def test_can_register_manual_payment_on_customer_invoice(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("2.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.bookkeep(self.user)

        response = self.client.post(
            reverse("invoicing:invoice_register_payment", args=[invoice.pk]),
            {
                "payment_date": "2026-07-01",
                "amount": "2500.00",
                "payment_account": str(self.bank_account.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("2500.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("0.00"))
        self.assertIsNotNone(invoice.payment_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.bank_account.pk)
        entries = invoice.payment_transaction.entries.select_related("account")
        self.assertTrue(entries.filter(account__number="1510", credit=Decimal("2500.00")).exists())
        self.assertTrue(entries.filter(account__number="1930", debit=Decimal("2500.00")).exists())

    def test_invoice_create_preselects_customer_from_query_param(self):
        self.customer.default_payment_terms_days = 45
        self.customer.save(update_fields=["default_payment_terms_days"])
        response = self.client.get(f"{reverse('invoicing:invoice_create')}?customer={self.customer.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial.get("customer"), self.customer.pk)
        self.assertEqual(response.context["customer_payment_terms_map"].get(str(self.customer.pk)), 45)
        # Förfallodatum är ett redigerbart fält som bara förpopuleras utifrån
        # kundens betalningsvillkor - betalningsvillkor visas inte separat.
        self.assertNotIn("payment_terms_days", response.context["form"].fields)
        self.assertIn("due_date", response.context["form"].fields)
        expected_due_date = timezone.localdate() + timedelta(days=45)
        self.assertEqual(response.context["form"].initial.get("due_date"), expected_due_date)

    def test_invoice_create_stores_customer_payment_terms_on_the_invoice(self):
        self.customer.default_payment_terms_days = 45
        self.customer.save(update_fields=["default_payment_terms_days"])
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-08-10",
                "reference": "Anna",
                "notes": "Tack for ert fortroende",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)
        self.assertEqual(invoice.payment_terms_days, 45)
        # Förfallodatum går att sätta manuellt och skrivs inte över av servern.
        self.assertEqual(invoice.due_date, date(2026, 8, 10))

    def test_invoice_create_defaults_payment_terms_to_30_days_when_customer_has_none(self):
        self.customer.default_payment_terms_days = 0
        self.customer.save(update_fields=["default_payment_terms_days"])
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "reference": "Anna",
                "notes": "Tack for ert fortroende",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)
        self.assertEqual(invoice.payment_terms_days, 30)
        self.assertEqual(invoice.due_date, date(2026, 7, 26))

    def _pdf_text(self, response):
        from pypdf import PdfReader

        return "".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)

    def test_print_page_renders(self):
        self.company.bankgiro = "123-4567"
        self.company.plusgiro = "765432-1"
        self.company.company_icon = "company_icons/test-logo.png"
        self.company.save(update_fields=["bankgiro", "plusgiro", "company_icon"])

        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )

        response = self.client.get(reverse("invoicing:invoice_print", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = self._pdf_text(response)
        self.assertIn("FAKTURA", text)
        self.assertIn(invoice.invoice_number, text)
        self.assertIn(invoice.ocr_code, text)
        self.assertIn("Bankgiro", text)
        self.assertIn(invoice.customer.name, text)

    def test_invoice_qr_payload_uses_company_payment_settings(self):
        self.company.bankgiro = "123-4567"
        self.company.plusgiro = "765432-1"
        self.company.save(update_fields=["bankgiro", "plusgiro"])
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )

        payload = invoice.build_payment_qr_payload()
        self.assertEqual(payload["nme"], self.company.name)
        self.assertEqual(payload["cid"], self.company.org_number)
        self.assertEqual(payload["pt"], "BG")
        self.assertEqual(payload["acc"], "123-4567")
        self.assertEqual(payload["iref"], invoice.ocr_code)

    def test_can_create_invoice_with_text_line_between_item_lines(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Anna",
                "notes": "",
                "lines-TOTAL_FORMS": "2",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Konsultarbete juni",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
                "lines-1-description": "Period: juni 2026",
                "lines-1-sort_order": "1",
                "lines-1-line_type": "text",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)
        self.assertEqual(invoice.lines.count(), 2)
        self.assertEqual(invoice.subtotal_ex_vat, Decimal("2000.00"))
        self.assertEqual(invoice.vat_amount, Decimal("500.0000"))
        self.assertEqual(len(invoice.vat_summary), 1)

        text_line = invoice.lines.get(line_type=InvoiceLine.LINE_TYPE_TEXT)
        self.assertEqual(text_line.description, "Period: juni 2026")
        self.assertEqual(text_line.quantity, Decimal("0.00"))
        self.assertIsNone(text_line.article)

        invoice.bookkeep(self.user)
        entries = invoice.booked_transaction.entries.select_related("account")
        self.assertEqual(entries.count(), 3)

    def test_invoice_with_only_text_lines_cannot_be_created(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "",
                "notes": "",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-description": "Bara en textrad",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "text",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Invoice.objects.filter(company=self.company).exists())
        self.assertContains(response, "Lägg till minst en artikelrad")

    def test_print_page_renders_text_line(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            payment_terms_days=30,
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        invoice.lines.create(
            description="Period: juni 2026",
            line_type=InvoiceLine.LINE_TYPE_TEXT,
            quantity=Decimal("0.00"),
            unit_price=Decimal("0.00"),
            vat_rate=Decimal("0.00"),
            sort_order=1,
        )

        response = self.client.get(reverse("invoicing:invoice_print", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Period: juni 2026", self._pdf_text(response))

    def test_print_page_shows_recurring_period_label_automatically(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-06-01",
            due_date="2026-07-01",
            payment_terms_days=30,
            recurring_period_label="Juni 2026",
        )
        invoice.lines.create(
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )

        response = self.client.get(reverse("invoicing:invoice_print", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Period: Juni 2026", self._pdf_text(response))


class ReminderPrintTests(CompanyTestCase):
    user_email = "reminder-user@example.com"
    company_name = "Reminder AB"
    company_org_number = "556600-3344"
    # Påminnelser utgår alltid från dagens datum, så året måste omfatta idag.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.accounting_year = create_accounting_year(
            self.company, self.today - timedelta(days=200), self.today + timedelta(days=165)
        )
        accounts = create_accounts(
            self.company,
            [
                ("1510", "Kundfordringar", AccountClass.ASSET),
                ("2611", "Utgående moms 25%", AccountClass.EQUITY_LIABILITY),
                ("3041", "Försäljning tjänster 25%", AccountClass.REVENUE),
            ],
        )
        self.customer = Customer.objects.create(company=self.company, name="Sengångaren AB")
        self.article = Article.objects.create(
            company=self.company,
            name="Konsulttimme",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=accounts["3041"],
            is_active=True,
        )
        self.invoice = self._create_invoice(due_days_ago=10)
        self.invoice.bookkeep(self.user)

    def _create_invoice(self, *, due_days_ago):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date=self.today - timedelta(days=due_days_ago + 30),
            due_date=self.today - timedelta(days=due_days_ago),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        return invoice

    def test_reminder_pdf_downloads_and_creates_a_history_row(self):
        response = self.client.post(
            reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
            {"avgift": "60.00", "betala_senast": (self.today + timedelta(days=10)).isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("paminnelse", response["Content-Disposition"])

        reminder = self.invoice.reminders.get()
        self.assertEqual(reminder.fee, Decimal("60.00"))
        self.assertEqual(reminder.pay_by_date, self.today + timedelta(days=10))
        self.assertEqual(reminder.created_by, self.user)
        self.assertEqual(reminder.sequence_number, 1)

    def test_second_reminder_gets_sequence_number_two(self):
        for _ in range(2):
            response = self.client.post(
                reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
                {"avgift": "60.00"},
            )
            self.assertEqual(response.status_code, 200)

        reminders = list(self.invoice.reminders.all())
        self.assertEqual([reminder.sequence_number for reminder in reminders], [1, 2])


class InvoiceEmailTests(ReminderPrintTests):
    """Skicka faktura/påminnelse via e-post — återanvänder ReminderPrintTests fixtur
    (bokförd, förfallen faktura) och kör även dess tester med e-postkonfigurationen satt."""

    user_email = "email-user@example.com"
    company_name = "E-postfaktura AB"
    company_fields = {
        "email_send_provider": "smtp",
        "email_send_from": "faktura@epostfaktura.se",
        "email_send_smtp_host": "smtp.example.com",
    }

    def setUp(self):
        super().setUp()
        self.customer.email = "kund@example.com"
        self.customer.save()

    def _post_email(self):
        with patch("django.core.mail.message.EmailMessage.send", return_value=1):
            return self.client.post(reverse("invoicing:invoice_email", args=[self.invoice.pk]), follow=True)

    def test_sends_invoice_pdf_and_logs_row_on_detail_page(self):
        response = self._post_email()
        self.assertContains(response, "Fakturan skickades till kund@example.com")

        sent = SentEmail.objects.get()
        self.assertEqual(sent.status, SentEmail.Status.SENT)
        self.assertEqual(sent.purpose, SentEmail.Purpose.INVOICE)
        self.assertEqual(sent.invoice, self.invoice)
        self.assertEqual(sent.recipient, "kund@example.com")
        self.assertContains(response, "Skickade e-postmeddelanden")

    def test_customer_without_email_is_rejected(self):
        self.customer.email = ""
        self.customer.save()
        response = self.client.post(reverse("invoicing:invoice_email", args=[self.invoice.pk]), follow=True)
        self.assertContains(response, "Kunden saknar e-postadress.")
        self.assertEqual(SentEmail.objects.count(), 0)

    def test_unconfigured_company_is_rejected(self):
        self.company.email_send_provider = ""
        self.company.save()
        response = self.client.post(reverse("invoicing:invoice_email", args=[self.invoice.pk]), follow=True)
        self.assertContains(response, "Utgående e-post är inte konfigurerad")
        self.assertEqual(SentEmail.objects.count(), 0)

    def test_unbooked_invoice_is_rejected(self):
        unbooked = self._create_invoice(due_days_ago=5)
        response = self.client.post(reverse("invoicing:invoice_email", args=[unbooked.pk]), follow=True)
        self.assertContains(response, "Bara en bokförd faktura")
        self.assertEqual(SentEmail.objects.count(), 0)

    def test_reminder_email_registers_reminder_and_sends(self):
        with patch("django.core.mail.message.EmailMessage.send", return_value=1):
            response = self.client.post(
                reverse("invoicing:invoice_reminder_email", args=[self.invoice.pk]),
                {"avgift": "60.00", "betala_senast": (self.today + timedelta(days=10)).isoformat()},
                follow=True,
            )
        self.assertContains(response, "Påminnelsen registrerades och skickades till kund@example.com")

        reminder = self.invoice.reminders.get()
        self.assertEqual(reminder.fee, Decimal("60.00"))
        sent = SentEmail.objects.get()
        self.assertEqual(sent.purpose, SentEmail.Purpose.REMINDER)
        self.assertEqual(sent.status, SentEmail.Status.SENT)

    def test_send_failure_shows_error_and_logs_failed_row(self):
        with patch("django.core.mail.message.EmailMessage.send", side_effect=OSError("Connection refused")):
            response = self.client.post(reverse("invoicing:invoice_email", args=[self.invoice.pk]), follow=True)
        self.assertContains(response, "Kunde inte skicka fakturan")
        self.assertEqual(SentEmail.objects.get().status, SentEmail.Status.FAILED)

    def test_reprint_returns_pdf_without_creating_a_new_reminder(self):
        self.client.post(reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]), {"avgift": "60.00"})
        reminder = self.invoice.reminders.get()

        response = self.client.get(reverse("invoicing:invoice_reminder_reprint", args=[self.invoice.pk, reminder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(self.invoice.reminders.count(), 1)

    def test_detail_page_lists_reminder_history(self):
        self.client.post(
            reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
            {"avgift": "60.00", "betala_senast": (self.today + timedelta(days=10)).isoformat()},
        )

        response = self.client.get(reverse("invoicing:invoice_detail", args=[self.invoice.pk]))

        self.assertContains(response, "Skickade påminnelser")
        self.assertContains(response, "Skriv ut påminnelse 2")

    def test_detail_page_warns_about_inkasso_after_two_reminders(self):
        for _ in range(2):
            self.client.post(
                reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
                {"avgift": "60.00", "betala_senast": (self.today + timedelta(days=10)).isoformat()},
            )

        response = self.client.get(reverse("invoicing:invoice_detail", args=[self.invoice.pk]))

        self.assertContains(response, "inkassovarsel eller inkassokrav")

    def test_detail_page_does_not_warn_about_inkasso_after_one_reminder(self):
        self.client.post(
            reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
            {"avgift": "60.00", "betala_senast": (self.today + timedelta(days=10)).isoformat()},
        )

        response = self.client.get(reverse("invoicing:invoice_detail", args=[self.invoice.pk]))

        self.assertNotContains(response, "inkassovarsel")

    def test_reminder_qr_uses_remaining_amount_plus_fee_and_new_due_date(self):
        pay_by = self.today + timedelta(days=10)
        payload = self.invoice.build_payment_qr_payload(amount=Decimal("1310.00"), due_date=pay_by)

        self.assertEqual(payload["due"], 1310.00)
        self.assertEqual(payload["ddt"], pay_by.strftime("%Y%m%d"))

    def test_reminder_is_refused_for_unbooked_and_paid_invoices(self):
        unbooked = self._create_invoice(due_days_ago=5)
        response = self.client.post(reverse("invoicing:invoice_reminder_print", args=[unbooked.pk]))
        self.assertRedirects(response, reverse("invoicing:invoice_detail", args=[unbooked.pk]))

        self.invoice.is_paid = True
        self.invoice.save(update_fields=["is_paid"])
        response = self.client.post(reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]))
        self.assertRedirects(response, reverse("invoicing:invoice_detail", args=[self.invoice.pk]))
        self.assertEqual(self.invoice.reminders.count(), 0)

    def test_negative_fee_is_rejected(self):
        response = self.client.post(
            reverse("invoicing:invoice_reminder_print", args=[self.invoice.pk]),
            {"avgift": "-5"},
        )
        self.assertRedirects(response, reverse("invoicing:invoice_detail", args=[self.invoice.pk]))
        self.assertEqual(self.invoice.reminders.count(), 0)

    def test_reminder_fee_default_comes_from_company_setting(self):
        self.company.reminder_fee = Decimal("75.50")
        self.company.save(update_fields=["reminder_fee"])

        response = self.client.get(reverse("invoicing:invoice_detail", args=[self.invoice.pk]))

        self.assertContains(response, 'value="75.50"')

    def test_detail_page_shows_reminder_form_only_when_overdue(self):
        response = self.client.get(reverse("invoicing:invoice_detail", args=[self.invoice.pk]))
        self.assertContains(response, "Skriv ut påminnelse")

        not_due = self._create_invoice(due_days_ago=-30)
        not_due.bookkeep(self.user)
        response = self.client.get(reverse("invoicing:invoice_detail", args=[not_due.pk]))
        self.assertNotContains(response, "Skriv ut påminnelse")


class RecurringInvoiceTests(CompanyTestCase):
    user_email = "recurring-invoice-user@example.com"
    company_name = "Recurring AB"
    company_org_number = "556600-1122"

    def setUp(self):
        super().setUp()
        # The invoicing code reads this one by its own name.
        self.accounting_year = self.year

        self.customer = Customer.objects.create(
            company=self.company,
            name="Kundbolaget AB",
            org_number="556611-2233",
            is_active=True,
        )

        accounts = create_accounts(
            self.company,
            [
                ("1510", "Kundfordringar", AccountClass.ASSET),
                ("2611", "Utgående moms 25%", AccountClass.EQUITY_LIABILITY),
                ("3041", "Försäljning tjänster 25%", AccountClass.REVENUE),
            ],
        )
        self.income_account = accounts["3041"]
        self.article = Article.objects.create(
            company=self.company,
            article_number="A100",
            name="Serviceavtal",
            unit="mån",
            unit_price=Decimal("5000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.income_account,
            is_active=True,
        )

    def _create_recurring(self, **overrides):
        defaults = {
            "company": self.company,
            "customer": self.customer,
            "name": "Månadsavtal",
            "interval": RecurringInvoiceInterval.MONTHLY,
            "start_date": date(2026, 1, 15),
            "next_run_date": date(2026, 1, 15),
            "payment_terms_days": 30,
        }
        defaults.update(overrides)
        recurring = RecurringInvoice.objects.create(**defaults)
        recurring.lines.create(
            article=self.article,
            description="Serviceavtal",
            quantity=Decimal("1.00"),
            unit="mån",
            unit_price=Decimal("5000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        recurring.lines.create(
            description="Period: {period}",
            line_type=InvoiceLine.LINE_TYPE_TEXT,
            sort_order=1,
        )
        return recurring

    def test_generate_invoice_creates_invoice_and_advances_next_run_date(self):
        recurring = self._create_recurring()

        invoice = recurring.generate_invoice()
        invoice.bookkeep(self.user)

        self.assertEqual(invoice.customer, self.customer)
        self.assertEqual(invoice.invoice_date.isoformat(), "2026-01-15")
        self.assertEqual(invoice.due_date.isoformat(), "2026-02-14")
        self.assertEqual(invoice.recurring_invoice_id, recurring.pk)
        self.assertEqual(invoice.recurring_period_label, "2026-01-15 – 2026-02-14")
        self.assertTrue(invoice.is_booked)
        self.assertEqual(invoice.subtotal_ex_vat, Decimal("5000.00"))

        text_line = invoice.lines.get(line_type=InvoiceLine.LINE_TYPE_TEXT)
        self.assertEqual(text_line.description, "Period: 2026-01-15 – 2026-02-14")

        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-02-15")

    def test_generate_invoice_interval_variants(self):
        cases = [
            (RecurringInvoiceInterval.QUARTERLY, None, "2026-04-15"),
            (RecurringInvoiceInterval.SEMIANNUAL, None, "2026-07-15"),
            (RecurringInvoiceInterval.ANNUAL, None, "2027-01-15"),
            (RecurringInvoiceInterval.CUSTOM_MONTHS, 2, "2026-03-15"),
        ]
        for interval, custom_months, expected_next_run in cases:
            with self.subTest(interval=interval):
                recurring = self._create_recurring(
                    name=f"Avtal {interval}",
                    interval=interval,
                    custom_interval_months=custom_months,
                )
                recurring.generate_invoice()
                recurring.refresh_from_db()
                self.assertEqual(recurring.next_run_date.isoformat(), expected_next_run)

    def test_generate_invoice_raises_when_inactive(self):
        recurring = self._create_recurring(is_active=False)
        with self.assertRaises(ValidationError):
            recurring.generate_invoice()

    def test_generate_invoice_raises_when_end_date_passed(self):
        recurring = self._create_recurring(next_run_date=date(2026, 3, 1), end_date=date(2026, 2, 1))
        with self.assertRaises(ValidationError):
            recurring.generate_invoice()

    def test_recurring_invoice_generate_view_books_and_redirects(self):
        recurring = self._create_recurring()

        response = self.client.post(reverse("invoicing:recurring_invoice_generate", args=[recurring.pk]))
        self.assertEqual(response.status_code, 302)

        invoice = Invoice.objects.get(recurring_invoice=recurring)
        self.assertTrue(invoice.is_booked)
        self.assertEqual(response["Location"], reverse("invoicing:invoice_detail", args=[invoice.pk]))

        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-02-15")

    def test_recurring_invoice_with_generated_invoices_cannot_be_deleted(self):
        recurring = self._create_recurring()
        recurring.generate_invoice()

        response = self.client.post(reverse("invoicing:recurring_invoice_delete", args=[recurring.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(RecurringInvoice.objects.filter(pk=recurring.pk).exists())

    def test_recurring_invoice_toggle_active(self):
        recurring = self._create_recurring()
        self.assertTrue(recurring.is_active)

        self.client.post(reverse("invoicing:recurring_invoice_toggle_active", args=[recurring.pk]))
        recurring.refresh_from_db()
        self.assertFalse(recurring.is_active)

        self.client.post(reverse("invoicing:recurring_invoice_toggle_active", args=[recurring.pk]))
        recurring.refresh_from_db()
        self.assertTrue(recurring.is_active)

    def test_can_create_recurring_invoice_with_text_and_item_lines(self):
        response = self.client.post(
            reverse("invoicing:recurring_invoice_create"),
            {
                "name": "Städavtal",
                "customer": self.customer.pk,
                "interval": "monthly",
                "custom_interval_months": "",
                "start_date": "2026-02-01",
                "end_date": "",
                "due_date_mode": "days_after",
                "period_reference": "current",
                "payment_terms_days": "30",
                "reference": "",
                "is_active": "on",
                "lines-TOTAL_FORMS": "2",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Städning",
                "lines-0-quantity": "1.00",
                "lines-0-unit": "mån",
                "lines-0-unit_price": "3000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
                "lines-1-description": "Period: {period}",
                "lines-1-sort_order": "1",
                "lines-1-line_type": "text",
            },
        )

        self.assertEqual(response.status_code, 302)
        recurring = RecurringInvoice.objects.get(company=self.company, name="Städavtal")
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-02-01")
        self.assertEqual(recurring.lines.count(), 2)

    def test_recurring_invoice_with_only_text_lines_cannot_be_created(self):
        response = self.client.post(
            reverse("invoicing:recurring_invoice_create"),
            {
                "name": "Ogiltig mall",
                "customer": self.customer.pk,
                "interval": "monthly",
                "custom_interval_months": "",
                "start_date": "2026-02-01",
                "end_date": "",
                "due_date_mode": "days_after",
                "period_reference": "current",
                "payment_terms_days": "30",
                "reference": "",
                "is_active": "on",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-description": "Bara text",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "text",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecurringInvoice.objects.filter(company=self.company, name="Ogiltig mall").exists())

    def test_generate_invoice_shows_whole_month_period_as_month_name(self):
        recurring = self._create_recurring(start_date=date(2026, 1, 1), next_run_date=date(2026, 1, 1))

        invoice = recurring.generate_invoice()

        self.assertEqual(invoice.recurring_period_label, "Januari 2026")
        text_line = invoice.lines.get(line_type=InvoiceLine.LINE_TYPE_TEXT)
        self.assertEqual(text_line.description, "Period: Januari 2026")

    def test_day_31_start_date_does_not_drift_without_month_end_anchor(self):
        recurring = self._create_recurring(start_date=date(2026, 1, 31), next_run_date=date(2026, 1, 31))

        recurring.generate_invoice()
        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-02-28")

        recurring.generate_invoice()
        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-03-31")

    def test_anchor_to_month_end_always_lands_on_last_day(self):
        recurring = self._create_recurring(
            start_date=date(2026, 1, 30), next_run_date=date(2026, 1, 30), anchor_to_month_end=True
        )

        recurring.generate_invoice()
        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-02-28")

        recurring.generate_invoice()
        recurring.refresh_from_db()
        self.assertEqual(recurring.next_run_date.isoformat(), "2026-03-31")

    def test_due_date_mode_day_of_month_rolls_to_next_month_when_past(self):
        recurring = self._create_recurring(
            start_date=date(2026, 1, 28),
            next_run_date=date(2026, 1, 28),
            due_date_mode="day_of_month",
            due_date_day_of_month=5,
        )

        invoice = recurring.generate_invoice()
        self.assertEqual(invoice.due_date.isoformat(), "2026-02-05")

    def test_due_date_mode_day_of_month_same_month_when_before_day(self):
        recurring = self._create_recurring(
            start_date=date(2026, 1, 1),
            next_run_date=date(2026, 1, 1),
            due_date_mode="day_of_month",
            due_date_day_of_month=25,
        )

        invoice = recurring.generate_invoice()
        self.assertEqual(invoice.due_date.isoformat(), "2026-01-25")

    def test_due_date_mode_last_day_of_month(self):
        recurring = self._create_recurring(
            start_date=date(2026, 2, 1),
            next_run_date=date(2026, 2, 1),
            due_date_mode="day_of_month",
            due_date_last_day_of_month=True,
        )

        invoice = recurring.generate_invoice()
        self.assertEqual(invoice.due_date.isoformat(), "2026-02-28")

    def test_can_create_recurring_invoice_with_day_of_month_due_date(self):
        response = self.client.post(
            reverse("invoicing:recurring_invoice_create"),
            {
                "name": "Hyresavtal",
                "customer": self.customer.pk,
                "interval": "monthly",
                "custom_interval_months": "",
                "start_date": "2026-02-01",
                "end_date": "",
                "due_date_mode": "day_of_month",
                "period_reference": "current",
                "due_date_day_choice": "last",
                "payment_terms_days": "30",
                "reference": "",
                "is_active": "on",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Hyra",
                "lines-0-quantity": "1.00",
                "lines-0-unit": "mån",
                "lines-0-unit_price": "5000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertEqual(response.status_code, 302)
        recurring = RecurringInvoice.objects.get(company=self.company, name="Hyresavtal")
        self.assertTrue(recurring.due_date_last_day_of_month)
        self.assertIsNone(recurring.due_date_day_of_month)

    def test_recurring_invoice_day_of_month_due_date_requires_day_choice(self):
        response = self.client.post(
            reverse("invoicing:recurring_invoice_create"),
            {
                "name": "Ogiltigt förfallodatum",
                "customer": self.customer.pk,
                "interval": "monthly",
                "custom_interval_months": "",
                "start_date": "2026-02-01",
                "end_date": "",
                "due_date_mode": "day_of_month",
                "period_reference": "current",
                "due_date_day_choice": "",
                "payment_terms_days": "30",
                "reference": "",
                "is_active": "on",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Hyra",
                "lines-0-quantity": "1.00",
                "lines-0-unit": "mån",
                "lines-0-unit_price": "5000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecurringInvoice.objects.filter(company=self.company, name="Ogiltigt förfallodatum").exists())

    def test_topbar_alert_bell_hidden_when_no_recurring_invoices_due(self):
        response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-ack-url="')

    def test_topbar_alert_bell_links_to_recurring_invoices_when_due(self):
        self._create_recurring(start_date=date(2020, 1, 1), next_run_date=date(2020, 1, 1))

        response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fixed-assets-alert-menu")
        self.assertContains(response, "recurring-invoices-alert-item")
        self.assertContains(response, reverse("invoicing:recurring_invoice_list"))

    def test_topbar_alert_not_shown_for_recurring_invoice_not_yet_due(self):
        today = timezone.localdate()
        self._create_recurring(start_date=today + timedelta(days=5), next_run_date=today + timedelta(days=5))

        response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-ack-url="')

    def test_generate_invoice_with_next_period_reference_bills_in_advance(self):
        recurring = self._create_recurring(
            start_date=date(2026, 1, 1), next_run_date=date(2026, 1, 1), period_reference="next"
        )

        invoice = recurring.generate_invoice()

        self.assertEqual(invoice.invoice_date.isoformat(), "2026-01-01")
        self.assertEqual(invoice.recurring_period_label, "Februari 2026")
        text_line = invoice.lines.get(line_type=InvoiceLine.LINE_TYPE_TEXT)
        self.assertEqual(text_line.description, "Period: Februari 2026")

    def test_generate_invoice_with_previous_period_reference_bills_in_arrears(self):
        recurring = self._create_recurring(
            start_date=date(2026, 1, 1), next_run_date=date(2026, 1, 1), period_reference="previous"
        )

        invoice = recurring.generate_invoice()

        self.assertEqual(invoice.invoice_date.isoformat(), "2026-01-01")
        self.assertEqual(invoice.recurring_period_label, "December 2025")

    def test_generate_invoice_default_period_reference_is_current(self):
        recurring = self._create_recurring(start_date=date(2026, 1, 1), next_run_date=date(2026, 1, 1))

        invoice = recurring.generate_invoice()

        self.assertEqual(invoice.recurring_period_label, "Januari 2026")


class InvoiceDoubleBookingTests(InvoicingWorkflowTests):
    def test_stale_instances_book_once(self):
        # Dubbelklick/dubbel POST: två request-instanser av samma faktura ska ge en verifikation.
        invoice = Invoice.objects.create(
            company=self.company, customer=self.customer, invoice_date=date(2026, 6, 1), due_date=date(2026, 7, 1)
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description="Rad",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        first = Invoice.objects.get(pk=invoice.pk).bookkeep(self.user)
        second = Invoice.objects.get(pk=invoice.pk).bookkeep(self.user)
        self.assertEqual(first.pk, second.pk)
