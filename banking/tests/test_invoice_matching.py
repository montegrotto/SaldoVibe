from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from banking.models import BankTransaction
from banking.services import get_manual_booking_invoice_options, get_quick_booking_suggestion
from banking.tests.base import BankingTestCase
from banking.views import book_transaction
from bookkeeping.company_scope import SESSION_COMPANY_KEY
from bookkeeping.models import JournalEntry, Transaction
from invoicing.models import Article, Customer, Invoice, InvoiceLine
from payroll.models import PayrollRun
from supplier_invoices.models import Supplier, SupplierInvoice


class InvoiceMatchingTests(BankingTestCase):
    def test_book_transaction_form_always_lists_unpaid_customer_invoices(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Valbar kund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-SEL-1",
            name="Tjanst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Forsaljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
            is_paid=False,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )

        # Outgoing payment direction and amount mismatch against customer invoice.
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Mismatch kund",
            amount="-300.00",
            external_id="bank-customer-select-1",
        )

        options = get_manual_booking_invoice_options(company=self.company, bank_tx=bank_tx)
        option_ids = {item["id"] for item in options["customer_invoice"]}

        self.assertIn(str(invoice.pk), option_ids)

    def test_book_transaction_form_always_lists_unpaid_supplier_invoices(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Valbar leverantor AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantorsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("1800.00"),
            description="Leverantorsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("1800.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-VAL-1800",
            invoice_date="2026-07-01",
            due_date="2026-07-11",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("1800.00"),
            total_amount=Decimal("1800.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )

        # Incoming payment direction and amount mismatch against supplier invoice.
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Mismatch leverantor",
            amount="500.00",
            external_id="bank-supplier-select-1",
        )

        options = get_manual_booking_invoice_options(company=self.company, bank_tx=bank_tx)
        option_ids = {item["id"] for item in options["supplier_invoice"]}

        self.assertIn(str(invoice.pk), option_ids)

    def test_customer_invoice_mode_rejects_outgoing_payment_direction(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Riktningskund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-DIR-1",
            name="Tjanst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Forsaljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
            is_paid=False,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Felriktad utbetalning",
            amount="-500.00",
            external_id="bank-customer-direction-1",
        )

        request = RequestFactory().post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_invoice_id": str(invoice.pk),
            },
        )
        request.user = self.user
        request.session = {SESSION_COMPANY_KEY: self.company.pk}
        setattr(request, "_messages", FallbackStorage(request))
        with patch("banking.views._render_book_transaction_form", return_value=HttpResponse("validation", status=200)):
            response = book_transaction(request, bank_tx.pk)

        self.assertEqual(response.status_code, 200)
        bank_tx.refresh_from_db()
        invoice.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertFalse(invoice.is_paid)

    def test_quick_booking_suggestion_for_customer_invoice_on_bank_account(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Kund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="TJANST-1",
            name="Konsulttjänst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Konsultarvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-11",
            description="Inbetalning kund",
            amount="1250.00",
            external_id="bank-customer-match-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.receivable_account)
        self.assertEqual(suggestion["rule_label"], f"Kundfaktura {invoice.invoice_number}")

    def test_quick_booking_suggestion_for_supplier_invoice_on_bank_account(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Leverantör AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantörsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("3500.00"),
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("3500.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-2026-100",
            invoice_date="2026-07-01",
            due_date="2026-07-12",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("3500.00"),
            total_amount=Decimal("3500.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Utbetalning leverantör",
            amount="-3500.00",
            external_id="bank-supplier-match-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.payable_account)
        self.assertEqual(suggestion["rule_label"], f"Leverantörsfaktura {invoice.invoice_number}")

    def test_quick_book_marks_supplier_invoice_paid_when_matched(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Betalningsleverantör AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantörsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("2500.00"),
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("2500.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-PAY-2500",
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Utbetalning leverantör",
            amount="-2500.00",
            external_id="bank-pay-invoice-1",
        )

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.bank_gl_account.id)
        self.assertEqual(str(invoice.payment_date), str(bank_tx.date))

    def test_manual_book_marks_supplier_invoice_paid_when_single_row_matches(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Manuell betalningsleverantör AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantörsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("1800.00"),
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("1800.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-MAN-1800",
            invoice_date="2026-07-01",
            due_date="2026-07-11",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("1800.00"),
            total_amount=Decimal("1800.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Utbetalning leverantör",
            amount="-1800.00",
            external_id="bank-manual-pay-invoice-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "counter_account[]": [str(self.payable_account.pk)],
                "counter_amount[]": ["1800.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.bank_gl_account.id)

    def test_book_transaction_can_post_against_supplier_invoice_mode(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Explicit leverantör AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantörsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("1800.00"),
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("1800.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-EXPL-1800",
            invoice_date="2026-07-01",
            due_date="2026-07-11",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("1800.00"),
            total_amount=Decimal("1800.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Utbetalning leverantör",
            amount="-1800.00",
            external_id="bank-explicit-supplier-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "supplier_invoice",
                "supplier_invoice_id": str(invoice.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)

    def test_book_transaction_supplier_invoice_partial_payment(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Delbetalningsleverantör AB",
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Leverantörsfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=Decimal("1800.00"),
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("1800.00"),
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-PART-1800",
            invoice_date="2026-07-01",
            due_date="2026-07-11",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("1800.00"),
            total_amount=Decimal("1800.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=booking_tx,
        )

        partial_bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Delutbetalning leverantör",
            amount="-600.00",
            external_id="bank-partial-supplier-1",
        )
        response = self.client.post(
            reverse("banking:book_transaction", args=[partial_bank_tx.pk]),
            {
                "booking_mode": "supplier_invoice",
                "supplier_invoice_id": str(invoice.pk),
            },
        )
        self.assertEqual(response.status_code, 302)

        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("600.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("1200.00"))

        final_bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-11",
            description="Slutbetalning leverantör",
            amount="-1200.00",
            external_id="bank-partial-supplier-2",
        )
        response = self.client.post(
            reverse("banking:book_transaction", args=[final_bank_tx.pk]),
            {
                "booking_mode": "supplier_invoice",
                "supplier_invoice_id": str(invoice.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("1800.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("0.00"))

    def test_quick_book_marks_customer_invoice_paid_when_matched(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Betalkund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-PAY-1",
            name="Tjänst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Kundinbetalning",
            amount="1250.00",
            external_id="bank-pay-customer-1",
        )

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.bank_gl_account.id)
        self.assertEqual(str(invoice.payment_date), str(bank_tx.date))

    def test_manual_book_marks_customer_invoice_paid_when_single_row_matches(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Manuell betalkund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-MAN-1",
            name="Tjänst",
            unit="h",
            unit_price=Decimal("640.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("800.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("800.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("640.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Kundinbetalning",
            amount="800.00",
            external_id="bank-manual-pay-customer-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "counter_account[]": [str(self.receivable_account.pk)],
                "counter_amount[]": ["800.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(invoice.payment_account_id, self.bank_gl_account.id)

    def test_book_transaction_can_post_against_customer_invoice_mode(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Explicit kund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-EXPL-1",
            name="Tjänst",
            unit="h",
            unit_price=Decimal("640.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("800.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("800.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("640.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Kundinbetalning",
            amount="800.00",
            external_id="bank-explicit-customer-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_invoice_id": str(invoice.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, bank_tx.booked_transaction_id)

    def test_book_transaction_customer_invoice_partial_payment(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Delbetalningskund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-PART-1",
            name="Tjänst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )

        partial_bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Delinbetalning kund",
            amount="500.00",
            external_id="bank-partial-customer-1",
        )
        response = self.client.post(
            reverse("banking:book_transaction", args=[partial_bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_invoice_id": str(invoice.pk),
            },
        )
        self.assertEqual(response.status_code, 302)

        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("750.00"))

        final_bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-11",
            description="Slutinbetalning kund",
            amount="750.00",
            external_id="bank-partial-customer-2",
        )
        response = self.client.post(
            reverse("banking:book_transaction", args=[final_bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_invoice_id": str(invoice.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("1250.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("0.00"))

    def test_book_transaction_customer_invoice_rounding_short_payment_closes_invoice(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Avrundningskund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="CUST-ROUND-1",
            name="Tjänst",
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description="Kundfaktura bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=booking_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Arvode",
            quantity=Decimal("1.00"),
            unit="h",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )

        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Kundinbetalning avrundad",
            amount="1249.00",
            external_id="bank-rounding-customer-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_invoice_id": str(invoice.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("1249.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("0.00"))
        entries = bank_tx.booked_transaction.entries.select_related("account")
        self.assertTrue(entries.filter(account__number="1930", debit=Decimal("1249.00")).exists())
        self.assertTrue(entries.filter(account__number="1510", credit=Decimal("1250.00")).exists())
        self.assertTrue(entries.filter(account__number="3740", debit=Decimal("1.00")).exists())

    def test_quick_booking_does_not_suggest_supplier_invoice_for_positive_amount(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="LevPartner AB",
            is_active=True,
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-2026-2500",
            invoice_date="2026-07-01",
            due_date="2026-07-12",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-09",
            description="Lev faktura",
            amount="2500.00",
            external_id="bank-supplier-positive-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNone(suggestion)

    def test_quick_booking_suggests_supplier_credit_invoice_for_positive_amount(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Kredit Leverantör AB",
            is_active=True,
        )
        credit_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-10",
            description="Leverantörskredit",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=credit_tx,
            account=self.payable_account,
            debit=Decimal("2500.00"),
            credit=Decimal("0.00"),
            description="Leverantörsskuld kredit",
        )
        JournalEntry.objects.create(
            transaction=credit_tx,
            account=self.counter_account,
            debit=Decimal("0.00"),
            credit=Decimal("2500.00"),
            description="Motkonto kredit",
        )
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-KR-2500",
            invoice_date="2026-07-01",
            due_date="2026-07-12",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            vat_amount=Decimal("0.00"),
            is_registered=True,
            is_paid=False,
            registered_transaction=credit_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-09",
            description="Återbetalning leverantör",
            amount="2500.00",
            external_id="bank-supplier-credit-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.payable_account)
        self.assertEqual(suggestion["rule_label"], f"Leverantörsfaktura {invoice.invoice_number}")

    def test_quick_booking_suggests_customer_credit_invoice_for_negative_amount(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Kund Kredit AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="KR-1",
            name="Kreditrad",
            unit="st",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        credit_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-11",
            description="Kundkredit",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=credit_tx,
            account=self.receivable_account,
            debit=Decimal("0.00"),
            credit=Decimal("1250.00"),
            description="Kundfordran kredit",
        )
        JournalEntry.objects.create(
            transaction=credit_tx,
            account=self.counter_account,
            debit=Decimal("1250.00"),
            credit=Decimal("0.00"),
            description="Motkonto kundkredit",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-12",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=True,
            booked_transaction=credit_tx,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Kreditfaktura",
            quantity=Decimal("1.00"),
            unit="st",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-12",
            description="Återbetalning till kund",
            amount="-1250.00",
            external_id="bank-customer-credit-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.receivable_account)
        self.assertEqual(suggestion["rule_label"], f"Kundfaktura {invoice.invoice_number}")

    def test_quick_booking_does_not_suggest_customer_invoice_draft(self):
        customer = Customer.objects.create(
            company=self.company,
            name="Utkastkund AB",
            default_payment_terms_days=30,
            is_active=True,
        )
        article = Article.objects.create(
            company=self.company,
            article_number="UTKAST-1",
            name="Utkastartikel",
            unit="st",
            unit_price=Decimal("2000.00"),
            vat_rate=Decimal("25.00"),
            income_account=self.counter_account,
            is_active=True,
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            payment_terms_days=30,
            accounting_year=self.year,
            receivable_account=self.receivable_account,
            is_booked=False,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            article=article,
            description="Utkastfaktura",
            quantity=Decimal("1.00"),
            unit="st",
            unit_price=Decimal("2000.00"),
            vat_rate=Decimal("25.00"),
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Inbetalning kund",
            amount="2500.00",
            external_id="bank-customer-draft-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNone(suggestion)

    def test_quick_booking_does_not_suggest_supplier_invoice_draft(self):
        supplier = Supplier.objects.create(
            company=self.company,
            name="Utkastleverantör AB",
            is_active=True,
        )
        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-UTKAST-1",
            invoice_date="2026-07-01",
            due_date="2026-07-10",
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            vat_amount=Decimal("0.00"),
            is_registered=False,
            is_paid=False,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-10",
            description="Utbetalning leverantör",
            amount="-2500.00",
            external_id="bank-supplier-draft-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNone(suggestion)

    def test_quick_booking_suggestion_for_payroll_run_on_bank_account(self):
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-25",
            description="Lönekörning 2026-07",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.salary_liability_account,
            debit=Decimal("0.00"),
            credit=Decimal("20000.00"),
            description="Nettolöneskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=Decimal("20000.00"),
            credit=Decimal("0.00"),
            description="Motkonto test",
        )
        PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date="2026-07-25",
            created_by=self.user,
            is_finished=True,
            finished_at=timezone.now(),
            finished_by=self.user,
            booking_transaction=booking_tx,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-26",
            description="Löneutbetalning",
            amount="-20000.00",
            external_id="bank-payroll-match-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.salary_liability_account)
        self.assertEqual(suggestion["rule_label"], "Löneutbetalning 2026-07")
