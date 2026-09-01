import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from attachments.models import TransactionAttachment
from bookkeeping.models import AccountClass, JournalEntry, PeriodLock
from saldovibe.testing import CompanyTestCase, create_accounting_year, create_accounts

from .models import Supplier, SupplierInvoice, SupplierInvoiceCostLine


class SupplierInvoiceWorkflowTests(CompanyTestCase):
    user_email = "invoice-user@example.com"
    company_name = "Invoice Company AB"
    company_org_number = "556677-8899"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-invoice-test-media-")
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
                ("2640", "Ingående moms", AccountClass.ASSET),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
                ("1930", "Företagskonto", AccountClass.ASSET),
            ],
        )
        self.expense_account = accounts["4010"]
        self.vat_account = accounts["2640"]
        self.payable_account = accounts["2440"]
        self.payment_account = accounts["1930"]
        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Office Supplies AB",
            org_number="556677-3344",
            email="invoice@office.example",
            phone="070-1234567",
            bankgiro="123-4567",
            is_active=True,
        )
        self.attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "invoice.pdf",
                b"%PDF-1.4 invoice",
                content_type="application/pdf",
            ),
        )

    def test_can_create_and_register_invoice_with_bookkeeping_entries(self):
        prior_year = create_accounting_year(self.company, "2025-01-01", "2025-12-31")

        response = self.client.post(
            reverse("supplier_invoices:invoice_create"),
            {
                "supplier": self.supplier.pk,
                "invoice_number": "INV-2026-001",
                "invoice_date": "2025-06-25",
                "due_date": "2026-07-25",
                "total_amount": "1250.00",
                "vat_amount": "250.00",
                "selected_attachment_ids": str(self.attachment.pk),
                "cost_lines-TOTAL_FORMS": "1",
                "cost_lines-INITIAL_FORMS": "0",
                "cost_lines-MIN_NUM_FORMS": "0",
                "cost_lines-MAX_NUM_FORMS": "1000",
                "cost_lines-0-expense_account": self.expense_account.pk,
                "cost_lines-0-debit": "1000.00",
                "register": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_invoices:invoice_list"))

        invoice = SupplierInvoice.objects.get(invoice_number="INV-2026-001")
        self.assertEqual(invoice.accounting_year_id, prior_year.pk)
        self.assertTrue(invoice.is_registered)
        self.assertIsNotNone(invoice.registered_transaction)
        self.assertEqual(invoice.supplier, self.supplier)
        self.assertEqual(invoice.supplier_name, self.supplier.name)
        self.assertEqual(invoice.vat_account_id, self.vat_account.pk)
        self.assertEqual(invoice.payable_account_id, self.payable_account.pk)
        self.assertEqual(invoice.attachments.count(), 1)

        self.assertEqual(invoice.total_amount, Decimal("1250.00"))
        self.assertEqual(invoice.cost_lines.count(), 1)

        entries = JournalEntry.objects.filter(transaction=invoice.registered_transaction)
        self.assertEqual(entries.count(), 3)

        total_debit = sum((e.debit for e in entries), Decimal("0.00"))
        total_credit = sum((e.credit for e in entries), Decimal("0.00"))
        self.assertEqual(total_debit, Decimal("1250.00"))
        self.assertEqual(total_credit, Decimal("1250.00"))

    def test_supplier_can_be_created_from_dedicated_supplier_page(self):
        response = self.client.post(
            reverse("supplier_invoices:supplier_create"),
            {
                "name": "New Modal Supplier AB",
                "org_number": "556699-1122",
                "email": "new@example.com",
                "phone": "070-7654321",
                "bankgiro": "987-6543",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("supplier_invoices:supplier_list"))
        self.assertTrue(
            Supplier.objects.filter(
                company=self.company,
                name="New Modal Supplier AB",
                bankgiro="987-6543",
            ).exists()
        )

    def test_invoice_can_be_saved_without_invoice_number_and_has_valid_qr_payload(self):
        response = self.client.post(
            reverse("supplier_invoices:invoice_create"),
            {
                "supplier": self.supplier.pk,
                "invoice_number": "",
                "ocr_code": "12345678901",
                "invoice_date": "2026-06-25",
                "due_date": "2026-07-25",
                "total_amount": "1000.00",
                "vat_amount": "200.00",
                "cost_lines-TOTAL_FORMS": "1",
                "cost_lines-INITIAL_FORMS": "0",
                "cost_lines-MIN_NUM_FORMS": "0",
                "cost_lines-MAX_NUM_FORMS": "1000",
                "cost_lines-0-expense_account": self.expense_account.pk,
                "cost_lines-0-debit": "800.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_invoices:invoice_list"))
        invoice = SupplierInvoice.objects.get(invoice_date="2026-06-25", ocr_code="12345678901")
        self.assertEqual(invoice.invoice_number, "")
        self.assertEqual(invoice.ocr_code, "12345678901")

        payload = invoice._build_payment_qr_payload()
        self.assertEqual(payload["uqr"], 2)
        self.assertEqual(payload["tp"], 1)
        self.assertEqual(payload["iref"], "12345678901")
        self.assertEqual(payload["pt"], "BG")
        self.assertEqual(payload["acc"], "123-4567")
        self.assertEqual(payload["due"], 1000.00)

    def test_invoice_qr_svg_endpoint_returns_svg(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-QR",
            ocr_code="9876543210",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )

        response = self.client.get(reverse("supplier_invoices:invoice_qr_svg", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("<svg", response.content.decode("utf-8"))

    def test_draft_invoice_can_be_registered_later(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-002",
            invoice_date="2026-06-26",
            due_date="2026-07-26",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("500.00"),
            total_amount=Decimal("625.00"),
            vat_amount=Decimal("125.00"),
            created_by=self.user,
        )
        invoice.cost_lines.create(expense_account=self.expense_account, debit=Decimal("500.00"))

        response = self.client.post(
            reverse("supplier_invoices:invoice_register", args=[invoice.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_invoices:invoice_list"))
        invoice.refresh_from_db()
        self.assertTrue(invoice.is_registered)
        self.assertIsNotNone(invoice.registered_transaction_id)

    def test_registered_invoice_can_be_paid_via_register_payment(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-003",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )
        invoice.cost_lines.create(expense_account=self.expense_account, debit=Decimal("400.00"))
        invoice.register_and_bookkeep(self.user)
        journal_entry_count_before = JournalEntry.objects.count()

        response = self.client.post(
            reverse("supplier_invoices:invoice_register_payment", args=[invoice.pk]),
            {
                "payment_date": "2026-06-30",
                "amount": "500.00",
                "payment_account": str(self.payment_account.pk),
            },
        )

        self.assertRedirects(response, reverse("supplier_invoices:invoice_list"))

        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertIsNotNone(invoice.payment_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.payment_account.pk)
        # Betalningsverifikationen: debet 2440 / kredit 1930.
        self.assertEqual(JournalEntry.objects.count(), journal_entry_count_before + 2)

    def test_manually_paid_invoice_can_be_unmarked(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-003-P",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )
        invoice.cost_lines.create(expense_account=self.expense_account, debit=Decimal("400.00"))
        invoice.register_and_bookkeep(self.user)
        # Legacy state from the old flag-only "markera som betald": paid without verifikation.
        invoice.is_paid = True
        invoice.paid_amount = Decimal("500.00")
        invoice.payment_date = date(2026, 6, 30)
        invoice.save(update_fields=["is_paid", "paid_amount", "payment_date"])

        response = self.client.post(
            reverse("supplier_invoices:invoice_unmark_manually_paid", args=[invoice.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("supplier_invoices:invoice_list"))

        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_invoice_detail_shows_attachment_panel(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-DETAIL",
            invoice_date=timezone.localdate(),
            due_date=timezone.localdate(),
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        SupplierInvoiceCostLine.objects.create(
            invoice=invoice, expense_account=self.expense_account, debit=Decimal("100.00")
        )
        invoice.attachments.add(self.attachment)

        response = self.client.get(reverse("supplier_invoices:invoice_detail", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INV-2026-DETAIL")
        self.assertContains(response, 'id="attachmentViewerFrame"')
        self.assertContains(response, self.attachment.file_name)

    def test_attachment_can_be_added_to_an_invoice_in_an_open_period(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-OPEN",
            invoice_date="2026-06-15",
            due_date="2026-07-15",
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        SupplierInvoiceCostLine.objects.create(
            invoice=invoice, expense_account=self.expense_account, debit=Decimal("100.00")
        )

        response = self.client.post(
            reverse("supplier_invoices:invoice_attachment_add", args=[invoice.pk]),
            {"selected_attachment_ids": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("supplier_invoices:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

    def test_attachment_cannot_be_added_or_removed_when_period_is_locked(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-LOCKED",
            invoice_date="2026-01-15",
            due_date="2026-02-15",
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        SupplierInvoiceCostLine.objects.create(
            invoice=invoice, expense_account=self.expense_account, debit=Decimal("100.00")
        )
        invoice.attachments.add(self.attachment)
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
            reverse("supplier_invoices:invoice_attachment_add", args=[invoice.pk]),
            {"selected_attachment_ids": str(other_attachment.pk)},
        )
        self.assertRedirects(add_response, reverse("supplier_invoices:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

        remove_response = self.client.post(
            reverse("supplier_invoices:invoice_attachment_remove", args=[invoice.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )
        self.assertRedirects(remove_response, reverse("supplier_invoices:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 1)

    def test_removing_an_attachment_only_unlinks_it_from_the_invoice(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-2026-UNLINK",
            invoice_date="2026-06-15",
            due_date="2026-07-15",
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("100.00"),
            total_amount=Decimal("100.00"),
        )
        SupplierInvoiceCostLine.objects.create(
            invoice=invoice, expense_account=self.expense_account, debit=Decimal("100.00")
        )
        invoice.attachments.add(self.attachment)

        response = self.client.post(
            reverse("supplier_invoices:invoice_attachment_remove", args=[invoice.pk]),
            {"attachment_id": str(self.attachment.pk)},
        )

        self.assertRedirects(response, reverse("supplier_invoices:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.attachments.count(), 0)
        self.attachment.refresh_from_db()
        self.assertIsNone(self.attachment.deleted_at)

    def test_invoice_list_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("supplier_invoices:invoice_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_invoice_with_multiple_cost_rows_creates_multiple_cost_entries(self):
        response = self.client.post(
            reverse("supplier_invoices:invoice_create"),
            {
                "supplier": self.supplier.pk,
                "invoice_number": "INV-2026-004",
                "invoice_date": "2026-07-01",
                "due_date": "2026-07-31",
                "total_amount": "1500.00",
                "vat_amount": "300.00",
                "cost_lines-TOTAL_FORMS": "2",
                "cost_lines-INITIAL_FORMS": "0",
                "cost_lines-MIN_NUM_FORMS": "0",
                "cost_lines-MAX_NUM_FORMS": "1000",
                "cost_lines-0-expense_account": self.expense_account.pk,
                "cost_lines-0-debit": "700.00",
                "cost_lines-1-expense_account": self.expense_account.pk,
                "cost_lines-1-debit": "500.00",
                "register": "1",
            },
        )

        self.assertRedirects(response, reverse("supplier_invoices:invoice_list"))
        invoice = SupplierInvoice.objects.get(invoice_number="INV-2026-004")
        entries = JournalEntry.objects.filter(transaction=invoice.registered_transaction)
        self.assertEqual(invoice.cost_lines.count(), 2)
        self.assertEqual(entries.count(), 4)

    def test_topbar_alert_shows_for_supplier_invoice_due_within_three_days(self):
        today = timezone.localdate()

        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-ALERT-003",
            invoice_date=today,
            due_date=today + timedelta(days=3),
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            vat_amount=Decimal("0.00"),
            created_by=self.user,
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fixed-assets-alert-menu")
        self.assertContains(response, "supplier-invoices-alert-item")


class InvoiceCreateExtractionSuggestionTests(CompanyTestCase):
    user_email = "extraction-user@example.com"
    company_name = "Extraktionsbolag AB"
    company_org_number = "556677-9900"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-invoice-extraction-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        create_accounts(
            self.company,
            [
                ("4010", "Inköp material", AccountClass.COST_OF_GOODS),
                ("2640", "Ingående moms", AccountClass.ASSET),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
            ],
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Kontorsmaterial AB",
            is_active=True,
        )

    def _attachment_with(self, extracted_data):
        return TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
            extracted_data=extracted_data,
        )

    def test_matched_vendor_prefills_supplier_and_invoice_fields(self):
        attachment = self._attachment_with(
            {
                "leverantör": "kontorsmaterial ab",  # skiftläge ska inte spela roll
                "totalbelopp": "625.00",
                "momsbelopp": "125.00",
                "fakturanummer": "F-2026-77",
                "ocr_referens": "1122334455",
                "datum": "2026-03-01",
                "förfallodatum": "2026-03-31",
            }
        )

        response = self.client.get(
            reverse("supplier_invoices:invoice_create"),
            {"selected_attachments": str(attachment.pk)},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["supplier"], str(self.supplier.pk))
        self.assertEqual(form.initial["invoice_number"], "F-2026-77")
        self.assertEqual(form.initial["ocr_code"], "1122334455")
        self.assertEqual(form.initial["invoice_date"], "2026-03-01")
        self.assertEqual(form.initial["due_date"], "2026-03-31")
        self.assertEqual(form.initial["total_amount"], "625.00")
        self.assertEqual(form.initial["vat_amount"], "125.00")
        self.assertTrue(response.context["extraction_applied"])
        self.assertEqual(response.context["extraction_unmatched_vendor_name"], "")
        self.assertContains(response, "ReInvGrabber")

    def test_unmatched_vendor_leaves_supplier_unset_and_offers_new_supplier_link(self):
        attachment = self._attachment_with({"leverantör": "Okänd Leverantör AB", "totalbelopp": "300.00"})

        response = self.client.get(
            reverse("supplier_invoices:invoice_create"),
            {"selected_attachments": str(attachment.pk)},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIsNone(form.initial["supplier"])
        self.assertEqual(form.initial["total_amount"], "300.00")
        self.assertEqual(response.context["extraction_unmatched_vendor_name"], "Okänd Leverantör AB")
        self.assertContains(response, "Okänd Leverantör AB")
        self.assertContains(response, "name=Ok%C3%A4nd%20Leverant%C3%B6r%20AB")

    def test_explicit_supplier_selection_is_not_overridden_by_extraction(self):
        attachment = self._attachment_with({"leverantör": "Ett Annat Bolag AB", "totalbelopp": "50.00"})

        response = self.client.get(
            reverse("supplier_invoices:invoice_create"),
            {"selected_attachments": str(attachment.pk), "supplier": str(self.supplier.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["supplier"], str(self.supplier.pk))

    def test_no_extraction_data_leaves_form_untouched(self):
        response = self.client.get(reverse("supplier_invoices:invoice_create"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["extraction_applied"])
        self.assertNotIn("invoice_number", response.context["form"].initial)

    def test_new_supplier_page_prefills_name_from_query_param(self):
        response = self.client.get(
            reverse("supplier_invoices:supplier_create"),
            {"name": "Okänd Leverantör AB"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["name"], "Okänd Leverantör AB")


class SupplierInvoiceDoubleBookingTests(SupplierInvoiceWorkflowTests):
    def test_stale_instances_register_once(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="INV-DBL",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )
        first = SupplierInvoice.objects.get(pk=invoice.pk).register_and_bookkeep(self.user)
        second = SupplierInvoice.objects.get(pk=invoice.pk).register_and_bookkeep(self.user)
        self.assertEqual(first.pk, second.pk)
