"""Bokslutsflödet: förkontroller, S1/S2-verifikationerna och wizard-vyn.

Låser besluten i docs/compliance/aarsavslut/bokslutsflode-design.md: serie S,
tvåverifikationsmodellen, kronologisk ordning, balanskontrollen och att
resultaträkningen visar 0 efter bokslut.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse

from bookkeeping.models import (
    AccountClass,
    AccountingYear,
    Company,
    JournalEntry,
    PeriodLock,
    Transaction,
    TransactionSource,
)
from bookkeeping.reports import build_income_statement_context
from bookkeeping.year_end import (
    annual_result,
    balance_difference,
    create_year_end_vouchers,
    is_year_closed,
    precheck_errors,
    year_end_voucher,
)
from saldovibe.testing import CompanyTestCase, create_accounting_year, create_accounts

ACCOUNT_SPECS = [
    ("1930", "Företagskonto", AccountClass.ASSET),
    ("2010", "Eget kapital", AccountClass.EQUITY_LIABILITY),
    ("2013", "Övriga egna uttag", AccountClass.EQUITY_LIABILITY),
    ("2019", "Årets resultat, delägare 1", AccountClass.EQUITY_LIABILITY),
    ("2091", "Balanserad vinst eller förlust", AccountClass.EQUITY_LIABILITY),
    ("2099", "Årets resultat", AccountClass.EQUITY_LIABILITY),
    ("3001", "Försäljning", AccountClass.REVENUE),
    ("5010", "Lokalhyra", AccountClass.OTHER_EXTERNAL),
    ("8999", "Årets resultat", AccountClass.FINANCIAL),
]


class YearEndTestCase(CompanyTestCase):
    user_fields = {"is_staff": True}
    company_fields = {"legal_form": Company.LegalForm.AKTIEBOLAG}

    def setUp(self):
        super().setUp()
        self.year.refresh_from_db()  # date-fält som date, inte str
        self.accounts = create_accounts(self.company, ACCOUNT_SPECS)

    def book(self, year, day, debit_number, credit_number, amount, source=TransactionSource.MANUAL):
        txn = Transaction.objects.create(
            accounting_year=year, date=day, description="Test", source=source, created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=self.accounts[debit_number], debit=Decimal(amount))
        JournalEntry.objects.create(transaction=txn, account=self.accounts[credit_number], credit=Decimal(amount))
        return txn

    def lock_through_november(self, year=None):
        year = year or self.year
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=year,
            period_start=year.start_date,
            period_end=date(year.end_date.year, 11, 30),
            reason="Test",
        )

    def create_next_year(self):
        return create_accounting_year(self.company, "2027-01-01", "2027-12-31")


class PrecheckTests(YearEndTestCase):
    def test_all_green_when_locked_and_balanced(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        self.lock_through_november()
        self.assertEqual(precheck_errors(self.company, self.year, None), [])

    def test_missing_legal_form_blocks(self):
        self.company.legal_form = ""
        self.company.save()
        self.lock_through_november()
        errors = precheck_errors(self.company, self.year, None)
        self.assertTrue(any("Bolagsform" in error for error in errors))

    def test_missing_locks_block(self):
        errors = precheck_errors(self.company, self.year, None)
        self.assertTrue(any("måste vara låsta" in error for error in errors))

    def test_fully_locked_year_blocks_s1(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=self.year.start_date,
            period_end=self.year.end_date,
            reason="Test",
        )
        errors = precheck_errors(self.company, self.year, None)
        self.assertTrue(any("låst period" in error for error in errors))

    def test_unclosed_earlier_year_blocks(self):
        earlier = create_accounting_year(self.company, "2025-01-01", "2025-12-31")
        self.book(earlier, date(2025, 3, 1), "1930", "3001", "50.00")
        self.lock_through_november()
        errors = precheck_errors(self.company, self.year, None)
        self.assertTrue(any("kronologisk ordning" in error for error in errors))

    def test_empty_earlier_year_counts_as_closed(self):
        create_accounting_year(self.company, "2025-01-01", "2025-12-31")
        self.lock_through_november()
        self.assertEqual(precheck_errors(self.company, self.year, None), [])

    def test_unbalanced_books_block(self):
        txn = Transaction.objects.create(
            accounting_year=self.year, date=date(2026, 3, 1), description="Halv", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=self.accounts["1930"], debit=Decimal("100.00"))
        self.lock_through_november()
        errors = precheck_errors(self.company, self.year, None)
        self.assertTrue(any("Balanskontrollen" in error for error in errors))


class CreateVouchersTests(YearEndTestCase):
    def test_ab_profit(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        next_year = self.create_next_year()

        s1, s2 = create_year_end_vouchers(self.company, self.user, self.year, next_year)

        self.assertEqual(s1.voucher_series, "S")
        self.assertEqual(s1.date, self.year.end_date)
        self.assertEqual(s1.source, TransactionSource.YEAR_END)
        entries = {entry.account.number: entry for entry in s1.entries.all()}
        self.assertEqual(entries["8999"].debit, Decimal("100.00"))
        self.assertEqual(entries["2099"].credit, Decimal("100.00"))

        self.assertEqual(s2.voucher_series, "S")
        self.assertEqual(s2.date, next_year.start_date)
        self.assertEqual(s2.accounting_year, next_year)
        entries = {entry.account.number: entry for entry in s2.entries.all()}
        self.assertEqual(entries["2099"].debit, Decimal("100.00"))
        self.assertEqual(entries["2091"].credit, Decimal("100.00"))

        # Efter bokslutet är både balansdifferensen och årets resultat noll.
        self.assertEqual(annual_result(self.year), Decimal("0.00"))
        self.assertEqual(balance_difference(self.year), Decimal("0.00"))
        self.assertTrue(is_year_closed(self.year))
        # S2 får inte markera nästa år som avslutat (datumvillkoret).
        self.assertFalse(is_year_closed(next_year))

    def test_ab_loss(self):
        self.book(self.year, date(2026, 3, 1), "5010", "1930", "80.00")
        next_year = self.create_next_year()

        s1, s2 = create_year_end_vouchers(self.company, self.user, self.year, next_year)

        entries = {entry.account.number: entry for entry in s1.entries.all()}
        self.assertEqual(entries["2099"].debit, Decimal("80.00"))
        self.assertEqual(entries["8999"].credit, Decimal("80.00"))
        entries = {entry.account.number: entry for entry in s2.entries.all()}
        self.assertEqual(entries["2091"].debit, Decimal("80.00"))
        self.assertEqual(entries["2099"].credit, Decimal("80.00"))

    def test_enskild_firma_zeroes_flow_accounts(self):
        self.company.legal_form = Company.LegalForm.ENSKILD_FIRMA
        self.company.save()
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        self.book(self.year, date(2026, 6, 1), "2013", "1930", "500.00")  # eget uttag
        next_year = self.create_next_year()

        s1, s2 = create_year_end_vouchers(self.company, self.user, self.year, next_year)

        entries = {entry.account.number: entry for entry in s1.entries.all()}
        self.assertEqual(entries["8999"].debit, Decimal("100.00"))
        self.assertEqual(entries["2019"].credit, Decimal("100.00"))

        entries = {entry.account.number: entry for entry in s2.entries.all()}
        self.assertEqual(entries["2013"].credit, Decimal("500.00"))
        self.assertEqual(entries["2019"].debit, Decimal("100.00"))
        self.assertEqual(entries["2010"].debit, Decimal("400.00"))
        self.assertTrue(s2.is_balanced)

    def test_requires_next_year(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        with self.assertRaises(ValidationError):
            create_year_end_vouchers(self.company, self.user, self.year, None)

    def test_refuses_double_close(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        next_year = self.create_next_year()
        create_year_end_vouchers(self.company, self.user, self.year, next_year)
        with self.assertRaises(ValidationError):
            create_year_end_vouchers(self.company, self.user, self.year, next_year)

    def test_corrected_s1_allows_rerun(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        next_year = self.create_next_year()
        s1, _ = create_year_end_vouchers(self.company, self.user, self.year, next_year)

        Transaction.objects.create(
            accounting_year=self.year,
            date=self.year.end_date,
            description="Korrigering",
            correction_of=s1,
            created_by=self.user,
        )
        self.assertIsNone(year_end_voucher(self.year))
        self.assertFalse(is_year_closed(self.year))


class IncomeStatementAfterCloseTests(YearEndTestCase):
    def test_result_is_zero_and_flagged_after_close(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        next_year = self.create_next_year()
        request = RequestFactory().get("/", {"year": self.year.pk})

        context = build_income_statement_context(request, self.company)
        self.assertEqual(context["annual_result"], Decimal("100.00"))
        self.assertFalse(context["year_is_closed"])

        create_year_end_vouchers(self.company, self.user, self.year, next_year)

        context = build_income_statement_context(request, self.company)
        self.assertEqual(context["annual_result"], Decimal("0.00"))
        self.assertTrue(context["year_is_closed"])


class WizardViewTests(YearEndTestCase):
    def url(self):
        return reverse("bookkeeping:year_end_close", kwargs={"pk": self.year.pk})

    def test_page_renders(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        self.lock_through_november()
        response = self.client.get(self.url())
        self.assertContains(response, "Förkontroller")
        self.assertContains(response, "Alla förkontroller är godkända")

    def test_create_next_year_action(self):
        response = self.client.post(self.url(), {"action": "create_next_year"})
        self.assertRedirects(response, self.url())
        self.assertTrue(AccountingYear.objects.filter(company=self.company, start_date=date(2027, 1, 1)).exists())

    def test_create_vouchers_action(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        self.lock_through_november()
        self.client.post(self.url(), {"action": "create_next_year"})

        response = self.client.post(self.url(), {"action": "create_vouchers"})
        self.assertRedirects(response, self.url())
        self.assertIsNotNone(year_end_voucher(self.year))

        response = self.client.get(self.url())
        self.assertContains(response, "Ingående balans")
        # IB-vyn visar bara balanskonton - resultatkontona hör inte hemma där.
        self.assertContains(response, "1930")
        self.assertNotContains(response, "3001")

    def test_create_vouchers_blocked_by_precheck(self):
        self.book(self.year, date(2026, 3, 1), "1930", "3001", "100.00")
        self.client.post(self.url(), {"action": "create_vouchers"})  # inga lås
        self.assertIsNone(year_end_voucher(self.year))

    def test_requires_finance_admin(self):
        self.user.is_staff = False
        self.user.save()
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse("bookkeeping:dashboard"))
