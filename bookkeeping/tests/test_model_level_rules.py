"""Modellnivå-skydd som backar upp vyernas/formulärens regler - de här ska hålla
även mot en service- eller shell-väg som aldrig passerar ett formulär."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from bookkeeping.models import (
    AccountClass,
    AccountingYear,
    JournalEntry,
    PeriodLock,
    Transaction,
)
from saldovibe.testing import CompanyTestCase, create_account, create_company


class ModelLevelRuleTests(CompanyTestCase):
    user_email = "model-rules@example.com"
    company_name = "Modellregler AB"
    company_org_number = "556677-4455"

    def setUp(self):
        super().setUp()
        self.debit_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.credit_account = create_account(self.company, "3001", "Försäljning", AccountClass.REVENUE)
        self.txn = Transaction.objects.create(
            accounting_year=self.year,
            date=date(2026, 6, 15),
            description="Testverifikation",
            created_by=self.user,
        )

    def test_journal_entry_rejects_negative_amounts_at_db_level(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.create(transaction=self.txn, account=self.debit_account, debit=Decimal("-100.00"))

    def test_journal_entry_clean_rejects_negative_amounts(self):
        entry = JournalEntry(transaction=self.txn, account=self.debit_account, credit=Decimal("-50.00"))
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_journal_entry_rejects_account_from_another_company(self):
        other_company = create_company("Annat Bolag AB", "556677-5566")
        other_account = create_account(other_company, "1930", "Företagskonto", AccountClass.ASSET)
        with self.assertRaises(ValidationError):
            JournalEntry.objects.create(transaction=self.txn, account=other_account, debit=Decimal("100.00"))

    def test_transaction_date_must_fall_within_accounting_year(self):
        with self.assertRaises(ValidationError):
            Transaction.objects.create(
                accounting_year=self.year,
                date=date(2027, 1, 1),
                description="Fel år",
                created_by=self.user,
            )

    def test_journal_entry_insert_into_locked_period_is_blocked_by_trigger(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            reason="Månadsavstämning",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.create(transaction=self.txn, account=self.debit_account, debit=Decimal("100.00"))

    def test_account_number_is_immutable_after_creation(self):
        self.debit_account.number = "1940"
        with self.assertRaises(ValidationError):
            self.debit_account.save()

    def test_account_other_fields_remain_editable(self):
        self.debit_account.name = "Nytt namn"
        self.debit_account.save()

    def test_accounting_year_clean_rejects_overlap(self):
        overlapping = AccountingYear(company=self.company, start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_accounting_year_clean_allows_adjacent_year(self):
        AccountingYear(company=self.company, start_date=date(2027, 1, 1), end_date=date(2027, 12, 31)).full_clean()

    def test_period_lock_clean_rejects_period_outside_year(self):
        lock = PeriodLock(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2025, 12, 1),
            period_end=date(2025, 12, 31),
            reason="Utanför året",
        )
        with self.assertRaises(ValidationError):
            lock.full_clean()

    def test_period_lock_clean_rejects_other_companys_year(self):
        other_company = create_company("Tredje Bolag AB", "556677-6677")
        lock = PeriodLock(
            company=other_company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Fel företag",
        )
        with self.assertRaises(ValidationError):
            lock.full_clean()


class BookedPayableDeleteTests(CompanyTestCase):
    user_email = "payable-delete@example.com"
    company_name = "Raderingsskydd AB"
    company_org_number = "556677-7788"

    def test_booked_invoice_cannot_be_deleted(self):
        from invoicing.models import Customer, Invoice

        customer = Customer.objects.create(company=self.company, name="Kund AB")
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=date(2026, 3, 1),
            due_date=date(2026, 3, 31),
            is_booked=True,
        )
        with self.assertRaises(ValidationError):
            invoice.delete()
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_unbooked_invoice_can_be_deleted(self):
        from invoicing.models import Customer, Invoice

        customer = Customer.objects.create(company=self.company, name="Kund 2 AB")
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=date(2026, 3, 1),
            due_date=date(2026, 3, 31),
        )
        invoice.delete()
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
