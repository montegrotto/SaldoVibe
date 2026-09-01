from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.urls import reverse

from banking.models import BankAccount, BankAccountType, BankTransaction
from banking.services import (
    get_transfer_link_candidates,
    link_bank_transaction_to_transaction,
    undo_bank_payment,
)
from banking.tests.base import BankingTestCase
from bookkeeping.models import JournalEntry, Transaction


class TransferLinkTests(BankingTestCase):
    """Internal transfer between two of the company's own bank accounts (e.g. Företagskonto ->
    Skattekonto): one side is booked normally, the other is linked to the verification the
    first side already created instead of booking a duplicate.
    """

    def setUp(self):
        super().setUp()
        self.tax_bank_account = BankAccount.objects.get(company=self.company, account_type=BankAccountType.TAX)

    def _book_source_transfer(self, amount="1000.00", external_id="transfer-source"):
        source_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-20",
            description="Överföring till skattekonto",
            amount=f"-{amount}",
            external_id=external_id,
        )
        self.client.post(
            reverse("banking:book_transaction", args=[source_tx.pk]),
            {
                "counter_account[]": [str(self.tax_gl_account.pk)],
                "counter_amount[]": [amount],
            },
        )
        source_tx.refresh_from_db()
        return source_tx

    def _create_destination_tx(self, amount="1000.00", external_id="transfer-dest"):
        return BankTransaction.objects.create(
            company=self.company,
            bank_account=self.tax_bank_account,
            date="2026-06-21",
            description="Insättning",
            amount=amount,
            external_id=external_id,
        )

    def test_link_candidates_find_matching_verification(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()

        candidates = get_transfer_link_candidates(company=self.company, bank_tx=destination_tx)

        self.assertEqual([c["id"] for c in candidates], [source_tx.booked_transaction_id])

    def test_link_view_reconciles_without_duplicate_journal_entries(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()
        verification = source_tx.booked_transaction

        response = self.client.post(
            reverse("banking:book_transaction", args=[destination_tx.pk]),
            {"booking_mode": "transfer", "transfer_transaction_id": str(verification.pk)},
        )

        self.assertEqual(response.status_code, 302)
        destination_tx.refresh_from_db()
        self.assertTrue(destination_tx.is_booked)
        self.assertEqual(destination_tx.booked_transaction_id, verification.pk)
        self.assertEqual(JournalEntry.objects.filter(transaction=verification).count(), 2)

        balances = {
            entry.account_id: entry.debit - entry.credit
            for entry in JournalEntry.objects.filter(transaction=verification)
        }
        self.assertEqual(balances[self.tax_gl_account.id], Decimal("1000.00"))
        self.assertEqual(balances[self.bank_gl_account.id], Decimal("-1000.00"))

    def test_link_rejects_amount_mismatch(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx(amount="999.00")

        self.assertEqual(get_transfer_link_candidates(company=self.company, bank_tx=destination_tx), [])
        with self.assertRaises(ValueError):
            link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=source_tx.booked_transaction)

    def test_cannot_double_link_same_bank_account(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()
        link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=source_tx.booked_transaction)

        second_destination_tx = self._create_destination_tx(external_id="transfer-dest-second")

        self.assertEqual(get_transfer_link_candidates(company=self.company, bank_tx=second_destination_tx), [])
        with self.assertRaises(ValueError):
            link_bank_transaction_to_transaction(
                bank_tx=second_destination_tx, transaction=source_tx.booked_transaction
            )

        with self.assertRaises(IntegrityError), db_transaction.atomic():
            second_destination_tx.booked_transaction = source_tx.booked_transaction
            second_destination_tx.is_booked = True
            second_destination_tx.save(update_fields=["booked_transaction", "is_booked"])

    def test_undo_releases_both_sides_of_transfer(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()
        link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=source_tx.booked_transaction)
        verification = source_tx.booked_transaction

        undo_bank_payment(verification, user=self.user, company=self.company)

        source_tx.refresh_from_db()
        destination_tx.refresh_from_db()
        self.assertFalse(source_tx.is_booked)
        self.assertIsNone(source_tx.booked_transaction_id)
        self.assertFalse(destination_tx.is_booked)
        self.assertIsNone(destination_tx.booked_transaction_id)

    def test_book_transaction_form_shows_transfer_mode_and_candidate(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()

        response = self.client.get(
            reverse("banking:book_transaction", args=[destination_tx.pk]), {"booking_mode": "transfer"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intern överföring")
        self.assertContains(response, str(source_tx.booked_transaction.voucher_number))

    def test_link_view_without_selection_shows_error(self):
        destination_tx = self._create_destination_tx()

        response = self.client.post(
            reverse("banking:book_transaction", args=[destination_tx.pk]),
            {"booking_mode": "transfer", "transfer_transaction_id": ""},
        )

        self.assertEqual(response.status_code, 200)
        destination_tx.refresh_from_db()
        self.assertFalse(destination_tx.is_booked)

    def test_link_candidates_exclude_verifications_not_booked_from_another_bank_account(self):
        # A manual verification that happens to touch 1630 for the right amount is not a
        # real transfer leg - it wasn't booked from any BankTransaction on another account.
        manual_txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-21",
            description="Manuell justering",
        )
        JournalEntry.objects.create(transaction=manual_txn, account=self.tax_gl_account, debit="1000.00", credit="0.00")
        JournalEntry.objects.create(
            transaction=manual_txn, account=self.counter_account, debit="0.00", credit="1000.00"
        )
        destination_tx = self._create_destination_tx()

        self.assertEqual(get_transfer_link_candidates(company=self.company, bank_tx=destination_tx), [])
        with self.assertRaises(ValueError):
            link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=manual_txn)

    def test_link_candidates_exclude_verifications_outside_date_window(self):
        source_tx = self._book_source_transfer(external_id="transfer-source-old")
        # 2026-06-20 source date vs. a destination row three weeks later - too far apart
        # to plausibly be the same transfer.
        destination_tx = self._create_destination_tx()
        destination_tx.date = "2026-07-11"
        destination_tx.save(update_fields=["date"])

        self.assertEqual(get_transfer_link_candidates(company=self.company, bank_tx=destination_tx), [])
        with self.assertRaises(ValueError):
            link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=source_tx.booked_transaction)

    def test_verification_detail_lists_both_linked_bank_transactions(self):
        source_tx = self._book_source_transfer()
        destination_tx = self._create_destination_tx()
        link_bank_transaction_to_transaction(bank_tx=destination_tx, transaction=source_tx.booked_transaction)
        verification = source_tx.booked_transaction

        response = self.client.get(reverse("bookkeeping:transaction_detail", args=[verification.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Länkat till")
        self.assertContains(response, self.bank_source.name)
        self.assertContains(response, self.tax_bank_account.name)
