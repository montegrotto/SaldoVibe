import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from bookkeeping.balances import build_account_balances
from bookkeeping.models import AccountClass, JournalEntry, Transaction
from saldovibe.testing import CompanyTestCase, create_account, create_accounting_year, create_company


class BuildAccountBalancesReferenceDateTests(TestCase):
    def setUp(self):
        self.company = create_company("Balances AB", "556000-0002")
        # 2024, not the shared default: the reference-date cutoffs below are dated.
        self.year = create_accounting_year(self.company, "2024-01-01", "2024-12-31")

        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.revenue_account = create_account(self.company, "3010", "Försäljning", AccountClass.REVENUE)

        old_tx = Transaction.objects.create(
            accounting_year=self.year, date="2024-01-06", description="Gammal insättning"
        )
        JournalEntry.objects.create(
            transaction=old_tx, account=self.bank_account, debit=Decimal("1000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=old_tx, account=self.revenue_account, debit=Decimal("0.00"), credit=Decimal("1000.00")
        )

        later_tx = Transaction.objects.create(accounting_year=self.year, date="2024-06-15", description="Senare uttag")
        JournalEntry.objects.create(
            transaction=later_tx, account=self.bank_account, debit=Decimal("0.00"), credit=Decimal("1500.00")
        )
        JournalEntry.objects.create(
            transaction=later_tx, account=self.revenue_account, debit=Decimal("1500.00"), credit=Decimal("0.00")
        )

    def test_balanskonto_excludes_entries_after_reference_date(self):
        balances = build_account_balances(self.company, reference_date="2024-01-06")
        self.assertEqual(Decimal(balances[str(self.bank_account.pk)]), Decimal("1000.00"))

    def test_balanskonto_includes_all_entries_when_reference_date_is_after_all(self):
        balances = build_account_balances(self.company, reference_date="2024-12-31")
        self.assertEqual(Decimal(balances[str(self.bank_account.pk)]), Decimal("-500.00"))

    def test_resultatkonto_excludes_entries_after_reference_date_within_year(self):
        balances = build_account_balances(self.company, reference_date="2024-01-06")
        self.assertEqual(Decimal(balances[str(self.revenue_account.pk)]), Decimal("-1000.00"))

    def test_resultatkonto_includes_all_entries_when_reference_date_is_after_all(self):
        balances = build_account_balances(self.company, reference_date="2024-12-31")
        self.assertEqual(Decimal(balances[str(self.revenue_account.pk)]), Decimal("500.00"))


class AccountBalancesLookupViewTests(CompanyTestCase):
    """Regression coverage for the Saldo column not refreshing when the user
    edits the verifikation/faktura date (bookkeeping:account_balances_lookup),
    which the entry-form JS fetches on that field's change event."""

    def setUp(self):
        super().setUp()
        self.wage_account = create_account(
            self.company, "7010", "Löner till kollektivanställda", AccountClass.PERSONNEL
        )
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

        january_tx = Transaction.objects.create(
            accounting_year=self.year, date="2026-01-10", description="Lönekörning januari"
        )
        JournalEntry.objects.create(
            transaction=january_tx, account=self.wage_account, debit=Decimal("5000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=january_tx, account=self.bank_account, debit=Decimal("0.00"), credit=Decimal("5000.00")
        )

        june_tx = Transaction.objects.create(
            accounting_year=self.year, date="2026-06-15", description="Lönekörning juni"
        )
        JournalEntry.objects.create(
            transaction=june_tx, account=self.wage_account, debit=Decimal("2000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=june_tx, account=self.bank_account, debit=Decimal("0.00"), credit=Decimal("2000.00")
        )

    def test_returns_balance_as_of_the_requested_date(self):
        url = reverse("bookkeeping:account_balances_lookup")
        response = self.client.get(url, {"datum": "2026-01-10"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(Decimal(data[str(self.wage_account.pk)]), Decimal("5000.00"))

    def test_later_date_includes_entries_up_to_that_date(self):
        url = reverse("bookkeeping:account_balances_lookup")
        response = self.client.get(url, {"datum": "2026-06-15"})
        data = json.loads(response.content)
        self.assertEqual(Decimal(data[str(self.wage_account.pk)]), Decimal("7000.00"))

    def test_missing_or_invalid_date_falls_back_without_error(self):
        url = reverse("bookkeeping:account_balances_lookup")
        response = self.client.get(url, {"datum": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn(str(self.wage_account.pk), data)
