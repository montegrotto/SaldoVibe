from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from banking.models import BankTransaction
from banking.services import get_quick_booking_suggestion, undo_bank_payment
from banking.tests.base import BankingTestCase
from bookkeeping.models import Account, AccountClass, JournalEntry, PeriodLock, Transaction
from bookkeeping.payables import offset_payables, offsettable_counterparts, register_manual_payment
from invoicing.models import Article, Customer, Invoice, InvoiceLine, InvoicePayment
from saldovibe.testing import create_accounts
from supplier_invoices.models import Supplier, SupplierInvoice, SupplierInvoicePayment


class PartialPaymentTestCase(BankingTestCase):
    def _create_customer_invoice(
        self, *, total, due_date="2026-07-10", name_suffix="1", vat_rate=Decimal("0.00"), customer_name=None
    ):
        # `total` is the ex-VAT line amount (negative for a credit invoice); the booked
        # receivable entry is the VAT-inclusive total, on the side matching its sign.
        customer, _ = Customer.objects.get_or_create(
            company=self.company,
            name=customer_name or f"Delbetalkund {name_suffix} AB",
            defaults={"default_payment_terms_days": 30, "is_active": True},
        )
        article, _ = Article.objects.get_or_create(
            company=self.company,
            article_number=f"PART-{name_suffix}",
            defaults={
                "name": "Tjänst",
                "unit": "h",
                "unit_price": Decimal("0.00"),
                "vat_rate": Decimal("0.00"),
                "income_account": self.counter_account,
                "is_active": True,
            },
        )
        total_incl = (total * (Decimal("100") + vat_rate) / Decimal("100")).quantize(Decimal("0.01"))
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description=f"Kundfaktura bokning {name_suffix}",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.receivable_account,
            debit=total_incl if total_incl >= 0 else Decimal("0.00"),
            credit=-total_incl if total_incl < 0 else Decimal("0.00"),
            description="Kundfordran",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=-total_incl if total_incl < 0 else Decimal("0.00"),
            credit=total_incl if total_incl >= 0 else Decimal("0.00"),
            description="Försäljning",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date="2026-07-01",
            due_date=due_date,
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
            unit_price=total,
            vat_rate=vat_rate,
        )
        return invoice

    def _create_supplier_invoice(self, *, total, due_date="2026-07-11", name_suffix="1"):
        supplier, _ = Supplier.objects.get_or_create(
            company=self.company,
            name=f"Delbetalleverantör {name_suffix} AB",
            defaults={"is_active": True},
        )
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-07-01",
            description=f"Leverantörsfaktura bokning {name_suffix}",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.payable_account,
            debit=Decimal("0.00"),
            credit=total,
            description="Leverantörsskuld",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=total,
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        return SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number=f"LEV-PART-{name_suffix}",
            invoice_date="2026-07-01",
            due_date=due_date,
            expense_account=self.counter_account,
            payable_account=self.payable_account,
            amount_ex_vat=total,
            total_amount=total,
            vat_amount=Decimal("0.00"),
            is_registered=True,
            registered_transaction=booking_tx,
        )

    def _create_bank_tx(self, *, amount, date="2026-07-10", external_id="bank-partial-x"):
        return BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date=date,
            description="Betalning",
            amount=amount,
            external_id=external_id,
        )


class ManualBookingAutoSettleTests(PartialPaymentTestCase):
    def test_manual_booking_to_unrelated_account_does_not_settle_invoice(self):
        invoice = self._create_customer_invoice(total=Decimal("800.00"))
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-manual-unrelated")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "manual",
                "counter_account[]": [str(self.counter_account.pk)],
                "counter_amount[]": ["800.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(InvoicePayment.objects.filter(payable=invoice).count(), 0)

    def test_manual_booking_row_on_receivable_settles_with_row_amount(self):
        invoice = self._create_customer_invoice(total=Decimal("800.00"))
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-manual-split-row")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "manual",
                "counter_account[]": [str(self.receivable_account.pk), str(self.counter_account.pk)],
                "counter_amount[]": ["500.00", "300.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        payment = InvoicePayment.objects.get(payable=invoice)
        self.assertEqual(payment.amount, Decimal("500.00"))


class InvoiceAllocationBookingTests(PartialPaymentTestCase):
    def test_split_bank_transaction_across_two_customer_invoices(self):
        first_invoice = self._create_customer_invoice(total=Decimal("500.00"), name_suffix="A")
        second_invoice = self._create_customer_invoice(total=Decimal("300.00"), name_suffix="B")
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-split-two")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(first_invoice.pk), str(second_invoice.pk)],
                "customer_alloc_amount[]": ["500.00", "300.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        first_invoice.refresh_from_db()
        second_invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertTrue(first_invoice.is_paid)
        self.assertTrue(second_invoice.is_paid)
        self.assertEqual(first_invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(second_invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(InvoicePayment.objects.filter(payable=first_invoice).count(), 1)
        self.assertEqual(InvoicePayment.objects.filter(payable=second_invoice).count(), 1)

        receivable_credit = sum(
            entry.credit
            for entry in JournalEntry.objects.filter(
                transaction=bank_tx.booked_transaction, account=self.receivable_account
            )
        )
        self.assertEqual(receivable_credit, Decimal("800.00"))

    def test_partial_allocation_with_explicit_amount(self):
        invoice = self._create_customer_invoice(total=Decimal("1250.00"))
        bank_tx = self._create_bank_tx(amount="500.00", external_id="bank-partial-alloc")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["500.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertFalse(invoice.is_paid)
        self.assertTrue(invoice.is_partially_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(invoice.remaining_amount, Decimal("750.00"))

    def test_fee_extra_row_with_negative_amount(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))
        fee_account = Account.objects.create(
            company=self.company,
            number="6570",
            name="Bankkostnader",
            account_class=AccountClass.OTHER_EXTERNAL_2,
            is_active=True,
        )
        bank_tx = self._create_bank_tx(amount="970.00", external_id="bank-fee-net")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["1000.00"],
                "customer_extra_account[]": [str(fee_account.pk)],
                "customer_extra_amount[]": ["-30.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("1000.00"))

        fee_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=fee_account)
        self.assertEqual(fee_entry.debit, Decimal("30.00"))
        receivable_entry = JournalEntry.objects.get(
            transaction=bank_tx.booked_transaction, account=self.receivable_account
        )
        self.assertEqual(receivable_entry.credit, Decimal("1000.00"))

    def test_overpayment_excess_to_extra_row(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))
        liability_account = Account.objects.create(
            company=self.company,
            number="2890",
            name="Övriga kortfristiga skulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        bank_tx = self._create_bank_tx(amount="1100.00", external_id="bank-overpay")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["1000.00"],
                "customer_extra_account[]": [str(liability_account.pk)],
                "customer_extra_amount[]": ["100.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        excess_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=liability_account)
        self.assertEqual(excess_entry.credit, Decimal("100.00"))

    def test_allocation_sum_mismatch_rejected(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-mismatch")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(invoice.pk)],
                "customer_alloc_amount[]": ["500.00"],
            },
        )

        self.assertEqual(response.status_code, 200)
        invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_supplier_write_off_closes_invoice(self):
        invoice = self._create_supplier_invoice(total=Decimal("1800.00"))
        bank_tx = self._create_bank_tx(amount="-1799.60", external_id="bank-supplier-writeoff")

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
        self.assertEqual(invoice.paid_amount, Decimal("1799.60"))

        payable_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=self.payable_account)
        self.assertEqual(payable_entry.debit, Decimal("1800.00"))
        rounding_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=self.rounding_account)
        self.assertEqual(rounding_entry.credit, Decimal("0.40"))

        payment = SupplierInvoicePayment.objects.get(payable=invoice)
        self.assertEqual(payment.amount, Decimal("1799.60"))
        self.assertEqual(payment.write_off_amount, Decimal("0.40"))

    def test_remaining_amount_is_zero_once_a_write_off_settles_the_invoice(self):
        """A written-off öresavrundning leaves paid_amount below total_amount.

        `remaining_amount` has to read that as settled, not as 0,40 kr still owing -
        the shortfall went to 3740, nothing is outstanding. All three payable types
        share one implementation in `PayableMixin`; this pins the purchase side, which
        used to answer with the difference while customer invoices answered zero.
        """
        supplier_invoice = self._create_supplier_invoice(total=Decimal("1800.00"), name_suffix="REM")
        bank_tx = self._create_bank_tx(amount="-1799.60", external_id="bank-supplier-remaining")

        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "supplier_invoice",
                "supplier_invoice_id": str(supplier_invoice.pk),
            },
        )

        supplier_invoice.refresh_from_db()
        self.assertTrue(supplier_invoice.is_paid)
        # Below the total precisely because 0,40 was written off, not left owing.
        self.assertEqual(supplier_invoice.paid_amount, Decimal("1799.60"))
        self.assertLess(supplier_invoice.paid_amount, supplier_invoice.total_amount)
        self.assertEqual(supplier_invoice.remaining_amount, Decimal("0.00"))

    def test_remaining_amount_answers_the_same_way_for_a_customer_invoice(self):
        customer_invoice = self._create_customer_invoice(total=Decimal("1800.00"), name_suffix="REM")
        bank_tx = self._create_bank_tx(amount="1799.60", external_id="bank-customer-remaining")

        self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "customer_invoice",
                "customer_alloc_invoice[]": [str(customer_invoice.pk)],
                "customer_alloc_amount[]": ["1799.60"],
            },
        )

        customer_invoice.refresh_from_db()
        self.assertTrue(customer_invoice.is_paid)
        self.assertEqual(customer_invoice.remaining_amount, Decimal("0.00"))

    def test_bank_partial_payments_create_history_rows(self):
        invoice = self._create_supplier_invoice(total=Decimal("1800.00"), name_suffix="HIST")

        first_tx = self._create_bank_tx(amount="-600.00", external_id="bank-hist-1")
        self.client.post(
            reverse("banking:book_transaction", args=[first_tx.pk]),
            {"booking_mode": "supplier_invoice", "supplier_invoice_id": str(invoice.pk)},
        )
        second_tx = self._create_bank_tx(amount="-1200.00", date="2026-07-11", external_id="bank-hist-2")
        self.client.post(
            reverse("banking:book_transaction", args=[second_tx.pk]),
            {"booking_mode": "supplier_invoice", "supplier_invoice_id": str(invoice.pk)},
        )

        invoice.refresh_from_db()
        first_tx.refresh_from_db()
        second_tx.refresh_from_db()
        self.assertTrue(invoice.is_paid)
        payments = list(SupplierInvoicePayment.objects.filter(payable=invoice).order_by("payment_date"))
        self.assertEqual(len(payments), 2)
        self.assertEqual(payments[0].amount, Decimal("600.00"))
        self.assertEqual(payments[0].transaction_id, first_tx.booked_transaction_id)
        self.assertEqual(payments[1].amount, Decimal("1200.00"))
        self.assertEqual(payments[1].transaction_id, second_tx.booked_transaction_id)
        self.assertEqual(invoice.payment_transaction_id, second_tx.booked_transaction_id)


class PeriodLockTests(PartialPaymentTestCase):
    def test_locked_period_blocks_bank_booking(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start="2026-07-01",
            period_end="2026-07-31",
            is_locked=True,
        )
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-locked")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "manual",
                "counter_account[]": [str(self.counter_account.pk)],
                "counter_amount[]": ["800.00"],
            },
            follow=True,
        )

        bank_tx.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        messages = [str(message) for message in response.context["messages"]]
        self.assertTrue(any("låst" in message for message in messages))


class SuggestionEngineTests(PartialPaymentTestCase):
    def test_suggestion_includes_partially_paid_invoice_outside_window(self):
        invoice = self._create_customer_invoice(total=Decimal("1250.00"))
        invoice.paid_amount = Decimal("500.00")
        invoice.save(update_fields=["paid_amount"])

        bank_tx = self._create_bank_tx(amount="750.00", date="2026-09-01", external_id="bank-late-partial")

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["source_type"], "customer_invoice")
        self.assertEqual(suggestion["source_id"], invoice.pk)


class MixedAllocationTests(PartialPaymentTestCase):
    def test_netting_supplier_invoice_against_customer_invoice(self):
        # Vi är skyldiga leverantören 500, kunden är skyldig oss 300; nettobetalningen är -200.
        supplier_invoice = self._create_supplier_invoice(total=Decimal("500.00"), name_suffix="NET")
        customer_invoice = self._create_customer_invoice(total=Decimal("300.00"), name_suffix="NET")
        bank_tx = self._create_bank_tx(amount="-200.00", external_id="bank-netting")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "match",
                "match_alloc_invoice[]": [
                    f"supplier_invoice:{supplier_invoice.pk}",
                    f"customer_invoice:{customer_invoice.pk}",
                ],
                "match_alloc_amount[]": ["500.00", "300.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        supplier_invoice.refresh_from_db()
        customer_invoice.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertTrue(supplier_invoice.is_paid)
        self.assertTrue(customer_invoice.is_paid)
        self.assertEqual(supplier_invoice.payment_transaction_id, bank_tx.booked_transaction_id)
        self.assertEqual(customer_invoice.payment_transaction_id, bank_tx.booked_transaction_id)

        payable_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=self.payable_account)
        self.assertEqual(payable_entry.debit, Decimal("500.00"))
        receivable_entry = JournalEntry.objects.get(
            transaction=bank_tx.booked_transaction, account=self.receivable_account
        )
        self.assertEqual(receivable_entry.credit, Decimal("300.00"))
        bank_entry = JournalEntry.objects.get(transaction=bank_tx.booked_transaction, account=self.bank_gl_account)
        self.assertEqual(bank_entry.credit, Decimal("200.00"))

    def test_match_mode_split_across_two_customer_invoices(self):
        first_invoice = self._create_customer_invoice(total=Decimal("500.00"), name_suffix="MA")
        second_invoice = self._create_customer_invoice(total=Decimal("300.00"), name_suffix="MB")
        bank_tx = self._create_bank_tx(amount="800.00", external_id="bank-match-split")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "match",
                "match_alloc_invoice[]": [
                    f"customer_invoice:{first_invoice.pk}",
                    f"customer_invoice:{second_invoice.pk}",
                ],
                "match_alloc_amount[]": ["500.00", "300.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        first_invoice.refresh_from_db()
        second_invoice.refresh_from_db()
        self.assertTrue(first_invoice.is_paid)
        self.assertTrue(second_invoice.is_paid)

    def test_match_mode_unbalanced_netting_rejected(self):
        supplier_invoice = self._create_supplier_invoice(total=Decimal("500.00"), name_suffix="NETX")
        customer_invoice = self._create_customer_invoice(total=Decimal("300.00"), name_suffix="NETX")
        bank_tx = self._create_bank_tx(amount="-250.00", external_id="bank-netting-bad")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "match",
                "match_alloc_invoice[]": [
                    f"supplier_invoice:{supplier_invoice.pk}",
                    f"customer_invoice:{customer_invoice.pk}",
                ],
                "match_alloc_amount[]": ["500.00", "300.00"],
            },
        )

        self.assertEqual(response.status_code, 200)
        bank_tx.refresh_from_db()
        supplier_invoice.refresh_from_db()
        customer_invoice.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        self.assertEqual(supplier_invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(customer_invoice.paid_amount, Decimal("0.00"))


class ManualPaymentTests(PartialPaymentTestCase):
    def test_register_manual_payment_customer_invoice(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))

        txn = register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1000.00"),
            payment_account=self.bank_gl_account,
        )
        invoice.refresh_from_db()

        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("1000.00"))
        self.assertEqual(invoice.payment_account, self.bank_gl_account)
        self.assertEqual(invoice.payment_transaction_id, txn.id)
        self.assertEqual(InvoicePayment.objects.filter(payable=invoice).count(), 1)
        self.assertEqual(txn.entries.get(account=self.receivable_account).credit, Decimal("1000.00"))
        self.assertEqual(txn.entries.get(account=self.bank_gl_account).debit, Decimal("1000.00"))

    def test_register_manual_payment_supplier_invoice(self):
        invoice = self._create_supplier_invoice(total=Decimal("500.00"), name_suffix="MAN")

        txn = register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("500.00"),
            payment_account=self.bank_gl_account,
        )
        invoice.refresh_from_db()

        self.assertTrue(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("500.00"))
        self.assertEqual(SupplierInvoicePayment.objects.filter(payable=invoice).count(), 1)
        self.assertEqual(txn.entries.get(account=self.payable_account).debit, Decimal("500.00"))
        self.assertEqual(txn.entries.get(account=self.bank_gl_account).credit, Decimal("500.00"))

    def test_register_manual_partial_payment_keeps_invoice_partial(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))

        register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("400.00"),
            payment_account=self.bank_gl_account,
        )
        invoice.refresh_from_db()

        self.assertFalse(invoice.is_paid)
        self.assertTrue(invoice.is_partially_paid)
        self.assertEqual(invoice.remaining_amount, Decimal("600.00"))

    def test_register_manual_payment_undo_resets_invoice(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))
        txn = register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1000.00"),
            payment_account=self.bank_gl_account,
        )

        undo_bank_payment(txn, user=self.user, company=self.company)
        invoice.refresh_from_db()

        self.assertFalse(invoice.is_paid)
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))

    def test_write_off_remainder_with_vat_adjustment(self):
        accounts = create_accounts(
            self.company,
            [
                ("6351", "Konstaterade kundförluster", AccountClass.OTHER_EXTERNAL),
                ("2611", "Utgående moms 25%", AccountClass.EQUITY_LIABILITY),
            ],
        )
        invoice = self._create_customer_invoice(total=Decimal("1000.00"), vat_rate=Decimal("25.00"), name_suffix="VAT")
        self.assertEqual(invoice.total_amount, Decimal("1250.00"))

        txn = register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1000.00"),
            payment_account=self.bank_gl_account,
            write_off_amount=Decimal("250.00"),
            write_off_account=accounts["6351"],
            adjust_vat=True,
        )
        invoice.refresh_from_db()

        self.assertTrue(invoice.is_paid)
        # 250 skrivs av: momsdelen 250 * 25/125 = 50 återtas på 2611, resten till 6351.
        self.assertEqual(txn.entries.get(account=self.receivable_account).credit, Decimal("1250.00"))
        self.assertEqual(txn.entries.get(account=self.bank_gl_account).debit, Decimal("1000.00"))
        self.assertEqual(txn.entries.get(account=accounts["2611"]).debit, Decimal("50.00"))
        self.assertEqual(txn.entries.get(account=accounts["6351"]).debit, Decimal("200.00"))

    def test_write_off_without_vat_adjustment_uses_single_account(self):
        accounts = create_accounts(
            self.company,
            [("6351", "Konstaterade kundförluster", AccountClass.OTHER_EXTERNAL)],
        )
        invoice = self._create_customer_invoice(total=Decimal("1000.00"), name_suffix="WOFF")

        txn = register_manual_payment(
            invoice,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("600.00"),
            payment_account=self.bank_gl_account,
            write_off_amount=Decimal("400.00"),
            write_off_account=accounts["6351"],
        )
        invoice.refresh_from_db()

        self.assertTrue(invoice.is_paid)
        self.assertEqual(txn.entries.get(account=accounts["6351"]).debit, Decimal("400.00"))

    def test_register_manual_payment_rejects_overpayment(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))

        with self.assertRaises(ValidationError):
            register_manual_payment(
                invoice,
                self.user,
                payment_date=date(2026, 7, 10),
                amount=Decimal("900.00"),
                payment_account=self.bank_gl_account,
                write_off_amount=Decimal("200.00"),
                write_off_account=self.rounding_account,
            )

    def test_register_manual_payment_rejects_locked_period(self):
        invoice = self._create_customer_invoice(total=Decimal("1000.00"))
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start="2026-07-01",
            period_end="2026-07-31",
            reason="Test",
            locked_by=self.user,
        )

        with self.assertRaises(ValidationError):
            register_manual_payment(
                invoice,
                self.user,
                payment_date=date(2026, 7, 10),
                amount=Decimal("1000.00"),
                payment_account=self.bank_gl_account,
            )


class OffsetPayableTests(PartialPaymentTestCase):
    def _invoice_pair(self):
        debit_invoice = self._create_customer_invoice(
            total=Decimal("1000.00"), name_suffix="DEB", customer_name="Kvittkund AB"
        )
        credit_invoice = self._create_customer_invoice(
            total=Decimal("-400.00"), name_suffix="KRED", customer_name="Kvittkund AB"
        )
        return debit_invoice, credit_invoice

    def test_offsettable_counterparts_lists_only_counter_sign_same_customer(self):
        debit_invoice, credit_invoice = self._invoice_pair()
        other_debit = self._create_customer_invoice(
            total=Decimal("300.00"), name_suffix="DEB2", customer_name="Kvittkund AB"
        )
        self._create_customer_invoice(total=Decimal("-100.00"), name_suffix="ANNAN")

        candidates = offsettable_counterparts(debit_invoice)
        self.assertEqual([candidate.pk for candidate in candidates], [credit_invoice.pk])

        candidates = offsettable_counterparts(credit_invoice)
        self.assertEqual(
            sorted(candidate.pk for candidate in candidates),
            sorted([debit_invoice.pk, other_debit.pk]),
        )

    def test_offset_settles_credit_invoice_and_reduces_debit_invoice(self):
        debit_invoice, credit_invoice = self._invoice_pair()

        txn = offset_payables(debit_invoice, credit_invoice, self.user, payment_date=date(2026, 7, 10))
        debit_invoice.refresh_from_db()
        credit_invoice.refresh_from_db()

        self.assertTrue(credit_invoice.is_paid)
        self.assertFalse(debit_invoice.is_paid)
        self.assertEqual(debit_invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(debit_invoice.remaining_amount, Decimal("600.00"))
        # Kvittningsverifikationen flyttar 400 kr inom kundfordringar: kredit för
        # debetfakturan, debet för kreditfakturan.
        entries = txn.entries.filter(account=self.receivable_account)
        self.assertEqual(sum(entry.credit for entry in entries), Decimal("400.00"))
        self.assertEqual(sum(entry.debit for entry in entries), Decimal("400.00"))
        self.assertEqual(InvoicePayment.objects.filter(transaction=txn).count(), 2)

    def test_offset_rejects_same_sign_invoices(self):
        debit_invoice = self._create_customer_invoice(
            total=Decimal("1000.00"), name_suffix="D1", customer_name="Kvittkund AB"
        )
        other_debit = self._create_customer_invoice(
            total=Decimal("300.00"), name_suffix="D2", customer_name="Kvittkund AB"
        )

        with self.assertRaises(ValidationError):
            offset_payables(debit_invoice, other_debit, self.user, payment_date=date(2026, 7, 10))

    def test_offset_undo_resets_both_invoices(self):
        debit_invoice, credit_invoice = self._invoice_pair()
        txn = offset_payables(debit_invoice, credit_invoice, self.user, payment_date=date(2026, 7, 10))

        undo_bank_payment(txn, user=self.user, company=self.company)
        debit_invoice.refresh_from_db()
        credit_invoice.refresh_from_db()

        self.assertFalse(credit_invoice.is_paid)
        self.assertEqual(debit_invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(credit_invoice.paid_amount, Decimal("0.00"))
