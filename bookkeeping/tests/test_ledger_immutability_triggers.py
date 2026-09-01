from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction

from bookkeeping.models import AccountClass, JournalEntry, PeriodLock, Transaction
from saldovibe.testing import CompanyTestCase, create_account


class LedgerImmutabilityTriggerTests(CompanyTestCase):
    """DB-level backstop for bookkeeping/admin.py's TransactionAdmin lockdown - these
    triggers hold even against a bug that uses .update()/.delete() on a queryset (which
    skips Django's signals) or direct DB access, not just the admin UI.
    See bookkeeping/migrations/0036_lock_posted_transactions.py.
    """

    user_email = "immutability@example.com"
    company_name = "Oföränderligt AB"
    company_org_number = "556677-3344"

    def setUp(self):
        super().setUp()
        self.debit_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.credit_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)
        self.txn = Transaction.objects.create(
            accounting_year=self.year,
            date=date(2026, 6, 26),
            description="Bokförd verifikation",
            created_by=self.user,
        )
        self.entry = JournalEntry.objects.create(
            transaction=self.txn, account=self.debit_account, debit=Decimal("100.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=self.txn, account=self.credit_account, debit=Decimal("0.00"), credit=Decimal("100.00")
        )

    def test_transaction_update_is_blocked(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Transaction.objects.filter(pk=self.txn.pk).update(description="Manipulerad")

    def test_journal_entry_update_is_blocked(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.filter(pk=self.entry.pk).update(debit=Decimal("999.00"))

    def test_deleting_open_period_original_still_clears_correction_of_via_cascade(self):
        correction = Transaction.objects.create(
            accounting_year=self.year,
            date=date(2026, 6, 27),
            description="Korrigering",
            created_by=self.user,
            correction_of=self.txn,
        )

        self.txn.delete()

        correction.refresh_from_db()
        self.assertIsNone(correction.correction_of_id)

    def test_deleting_the_creating_user_still_clears_created_by_via_cascade(self):
        self.user.delete()

        self.txn.refresh_from_db()
        self.assertIsNone(self.txn.created_by_id)

    def test_transaction_delete_is_allowed_in_open_period(self):
        self.txn.delete()

        self.assertFalse(Transaction.objects.filter(pk=self.txn.pk).exists())
        self.assertFalse(JournalEntry.objects.filter(transaction_id=self.txn.pk).exists())

    def test_transaction_delete_is_blocked_in_locked_period(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            reason="Juni stängd",
            locked_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.txn.delete()

        self.assertTrue(Transaction.objects.filter(pk=self.txn.pk).exists())

    def test_journal_entry_delete_is_blocked_in_locked_period(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            reason="Juni stängd",
            locked_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.entry.delete()

        self.assertTrue(JournalEntry.objects.filter(pk=self.entry.pk).exists())
