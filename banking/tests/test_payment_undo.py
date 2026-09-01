from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse

from banking.services import PaymentReversalError, get_payment_reversal_preview, undo_bank_payment
from banking.tests.test_partial_payments import PartialPaymentTestCase
from bookkeeping.models import JournalEntry, Transaction
from invoicing.models import InvoicePayment
from supplier_invoices.models import SupplierInvoicePayment


class UndoBankPaymentServiceTests(PartialPaymentTestCase):
    def test_undo_resets_invoice_and_releases_bank_transaction(self):
        invoice = self._create_customer_invoice(total=Decimal("800.00"))
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-undo-1")
        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["800.00"],
            },
        )
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        payment_transaction = bank_tx.booked_transaction
        self.assertTrue(invoice.is_paid)
        self.assertTrue(bank_tx.is_booked)

        reversal = undo_bank_payment(payment_transaction, user=self.user, company=self.company)

        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        payment_transaction.refresh_from_db()

        self.assertEqual(reversal.correction_of_id, payment_transaction.id)
        self.assertTrue(reversal.is_balanced)
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertIsNone(invoice.payment_transaction_id)
        self.assertFalse(bank_tx.is_booked)
        self.assertIsNone(bank_tx.booked_transaction_id)
        self.assertIsNone(bank_tx.booked_at)

        payment_row = InvoicePayment.objects.get(payable=invoice, transaction=payment_transaction)
        self.assertIsNotNone(payment_row.reversed_at)
        self.assertEqual(payment_row.reversal_transaction_id, reversal.id)

        # Original verification is untouched, not deleted or edited.
        original_entries = JournalEntry.objects.filter(transaction=payment_transaction)
        self.assertTrue(original_entries.exists())

    def test_undo_cascades_across_multiple_invoices_settled_by_same_transaction(self):
        first_invoice = self._create_customer_invoice(total=Decimal("500.00"), name_suffix="U1")
        second_invoice = self._create_customer_invoice(total=Decimal("300.00"), name_suffix="U2")
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-undo-multi")
        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(first_invoice.pk), str(second_invoice.pk)],
                "customer_alloc_amount[]": ["500.00", "300.00"],
            },
        )
        bank_tx.refresh_from_db()
        payment_transaction = bank_tx.booked_transaction

        preview = get_payment_reversal_preview(payment_transaction, company=self.company)
        self.assertEqual(len(preview["affected"]), 2)
        self.assertEqual(preview["bank_transactions"], [bank_tx])

        undo_bank_payment(payment_transaction, user=self.user, company=self.company)

        first_invoice.refresh_from_db()
        second_invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertFalse(first_invoice.is_paid)
        self.assertFalse(second_invoice.is_paid)
        self.assertFalse(bank_tx.is_booked)

    def test_undo_preserves_untouched_payment_from_another_transaction(self):
        invoice = self._create_supplier_invoice(total=Decimal("1000.00"), name_suffix="KEEP")

        first_tx = self._create_bank_tx(amount="-400.00", external_id="bank-keep-1")
        self.client.post(
            reverse("banking:book_transaction", args=[first_tx.pk]),
            {"booking_mode": "supplier_invoice", "supplier_invoice_id": str(invoice.pk)},
        )
        second_tx = self._create_bank_tx(amount="-600.00", date="2026-07-11", external_id="bank-keep-2")
        self.client.post(
            reverse("banking:book_transaction", args=[second_tx.pk]),
            {"booking_mode": "supplier_invoice", "supplier_invoice_id": str(invoice.pk)},
        )
        invoice.refresh_from_db()
        first_tx.refresh_from_db()
        second_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        first_payment_transaction_id = first_tx.booked_transaction_id
        second_payment_transaction_id = second_tx.booked_transaction_id

        # Undoing mutates the BankTransaction reached via the reverse accessor on the
        # Transaction we pass in — which Django's related-object cache makes the same
        # Python object as second_tx.booked_transaction, so capture ids above first.
        undo_bank_payment(second_tx.booked_transaction, user=self.user, company=self.company)

        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertTrue(invoice.is_partially_paid)
        self.assertEqual(invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(invoice.payment_transaction_id, first_payment_transaction_id)

        first_payment = SupplierInvoicePayment.objects.get(payable=invoice, transaction_id=first_payment_transaction_id)
        self.assertIsNone(first_payment.reversed_at)
        second_payment = SupplierInvoicePayment.objects.get(
            payable=invoice, transaction_id=second_payment_transaction_id
        )
        self.assertIsNotNone(second_payment.reversed_at)

    def test_undo_non_latest_payment_still_resets_invoice(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"), name_suffix="NONLATEST")

        first_tx = self._create_bank_tx(amount="500.00", external_id="bank-nonlatest-1")
        self.client.post(
            reverse("banking:book_transaction", args=[first_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["500.00"],
            },
        )
        second_tx = self._create_bank_tx(amount="500.00", date="2026-07-11", external_id="bank-nonlatest-2")
        self.client.post(
            reverse("banking:book_transaction", args=[second_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["500.00"],
            },
        )
        invoice.refresh_from_db()
        first_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.payment_transaction_id, invoice.payments.latest("id").transaction_id)

        first_payment_transaction = first_tx.booked_transaction

        preview = get_payment_reversal_preview(first_payment_transaction, company=self.company)
        self.assertEqual([row["object"].pk for row in preview["affected"]], [invoice.pk])

        undo_bank_payment(first_payment_transaction, user=self.user, company=self.company)

        invoice.refresh_from_db()
        first_tx.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertTrue(invoice.is_partially_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertFalse(first_tx.is_booked)

        first_payment_row = InvoicePayment.objects.get(payable=invoice, transaction=first_payment_transaction)
        self.assertIsNotNone(first_payment_row.reversed_at)

    def test_undo_twice_is_rejected(self):
        invoice = self._create_customer_invoice(total=Decimal("400.00"), name_suffix="U3")
        bank_tx = self._create_bank_tx(amount="400.00", external_id="bank-undo-twice")
        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["400.00"],
            },
        )
        bank_tx.refresh_from_db()
        payment_transaction = bank_tx.booked_transaction

        undo_bank_payment(payment_transaction, user=self.user, company=self.company)

        with self.assertRaises(PaymentReversalError):
            undo_bank_payment(payment_transaction, user=self.user, company=self.company)


class PaymentUndoViewTests(PartialPaymentTestCase):
    def test_undo_view_full_workflow(self):
        invoice = self._create_customer_invoice(total=Decimal("650.00"), name_suffix="V1")
        bank_tx = self._create_bank_tx(amount="650.00", external_id="bank-undo-view")
        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["650.00"],
            },
        )
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        payment_transaction = bank_tx.booked_transaction

        confirm_response = self.client.get(reverse("banking:payment_undo_confirm", args=[payment_transaction.pk]))
        self.assertEqual(confirm_response.status_code, 200)
        self.assertContains(confirm_response, str(invoice))

        undo_response = self.client.post(reverse("banking:payment_undo", args=[payment_transaction.pk]))
        self.assertEqual(undo_response.status_code, 302)

        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertFalse(bank_tx.is_booked)

        reversal = Transaction.objects.get(correction_of=payment_transaction)
        self.assertTrue(reversal.is_balanced)

    def test_confirm_page_still_lists_a_payable_type_it_has_no_detail_route_for(self):
        """The registry is derived from the models now, so a new payable type appears
        here on its own - before anyone adds a detail route for it. This page is the
        user's only view of what an undo will touch, so an unknown type must still be
        listed, just without a link.
        """
        invoice = self._create_customer_invoice(total=Decimal("400.00"), name_suffix="V2")
        bank_tx = self._create_bank_tx(amount="400.00", external_id="bank-undo-unknown-label")
        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["400.00"],
            },
        )
        bank_tx.refresh_from_db()
        payment_transaction = bank_tx.booked_transaction

        real_preview = get_payment_reversal_preview(payment_transaction, company=self.company)
        relabelled = {
            **real_preview,
            "affected": [{**item, "label": "Nyponfaktura"} for item in real_preview["affected"]],
        }

        with patch("banking.views.get_payment_reversal_preview", return_value=relabelled):
            response = self.client.get(reverse("banking:payment_undo_confirm", args=[payment_transaction.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nyponfaktura")
        self.assertContains(response, str(invoice))
