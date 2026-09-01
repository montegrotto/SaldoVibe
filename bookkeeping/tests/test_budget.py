from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from bookkeeping.forms import budget_field_name
from bookkeeping.models import AccountClass, BudgetLine, JournalEntry, Transaction
from bookkeeping.reports import build_income_statement_context
from saldovibe.testing import CompanyTestCase, create_account, create_accounting_year, create_company


class BudgetLineModelTests(CompanyTestCase):
    company_name = "Budgetmodellbolaget AB"
    company_org_number = "556677-5566"

    def setUp(self):
        super().setUp()
        self.revenue_account = create_account(self.company, "3041", "Försäljning", AccountClass.REVENUE)
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

    def test_str_includes_account_year_and_month(self):
        line = BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=3,
            amount=Decimal("1000.00"),
        )
        self.assertIn("3041", str(line))
        self.assertIn("03", str(line))

    def test_unique_constraint_blocks_duplicate_year_account_month(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=1,
            amount=Decimal("100.00"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BudgetLine.objects.create(
                    company=self.company,
                    accounting_year=self.year,
                    account=self.revenue_account,
                    month=1,
                    amount=Decimal("200.00"),
                )

    def test_clean_rejects_balance_sheet_account(self):
        line = BudgetLine(
            company=self.company,
            accounting_year=self.year,
            account=self.bank_account,
            month=1,
            amount=Decimal("500.00"),
        )
        with self.assertRaises(ValidationError):
            line.clean()

    def test_clean_rejects_account_and_year_from_different_companies(self):
        other_company = create_company("Annat bolag AB", "556677-9988")
        other_year = create_accounting_year(other_company)
        line = BudgetLine(
            company=self.company,
            accounting_year=other_year,
            account=self.revenue_account,
            month=1,
            amount=Decimal("500.00"),
        )
        with self.assertRaises(ValidationError):
            line.clean()


class BudgetEditViewTests(CompanyTestCase):
    company_name = "Budgetvyn AB"
    company_org_number = "556677-6677"

    def setUp(self):
        super().setUp()
        self.revenue_account = create_account(self.company, "3041", "Försäljning tjänster", AccountClass.REVENUE)
        self.cost_account = create_account(self.company, "6310", "Försäkringar", AccountClass.OTHER_EXTERNAL_2)
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        # Only accounts with postings (or an existing budget line) appear in the grid - see
        # BudgetForm's docstring - so give the two budgetable accounts a posting each.
        for account in (self.revenue_account, self.cost_account):
            txn = Transaction.objects.create(
                accounting_year=self.year, date="2026-03-15", description="Test", created_by=self.user
            )
            JournalEntry.objects.create(transaction=txn, account=account, credit=Decimal("100.00"))
            JournalEntry.objects.create(transaction=txn, account=self.bank_account, debit=Decimal("100.00"))

    def _url(self, year=None):
        return reverse("bookkeeping:budget_edit", args=[(year or self.year).pk])

    def test_get_renders_form_with_income_statement_accounts_only(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Försäljning tjänster")
        self.assertContains(response, "Försäkringar")
        self.assertNotContains(response, "Företagskonto")

    def test_unused_account_without_postings_or_budget_not_shown(self):
        """Regression guard: a company's full BAS chart has ~780 klass 3-10 accounts, so
        showing every one of them (rather than just those in use) would blow past
        DATA_UPLOAD_MAX_NUMBER_FIELDS on submit - see BudgetForm's docstring."""
        create_account(self.company, "6420", "Telefon", AccountClass.OTHER_EXTERNAL_2)

        response = self.client.get(self._url())

        self.assertNotContains(response, "Telefon")

    def test_account_with_only_a_budget_line_is_still_shown(self):
        unposted_account = create_account(self.company, "6420", "Telefon", AccountClass.OTHER_EXTERNAL_2)
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=unposted_account,
            month=1,
            amount=Decimal("-100.00"),
        )

        response = self.client.get(self._url())

        self.assertContains(response, "Telefon")

    def test_post_creates_budget_lines(self):
        data = {
            budget_field_name(self.revenue_account.pk, 1): "10000.00",
            budget_field_name(self.cost_account.pk, 1): "-500.00",
        }
        response = self.client.post(self._url(), data)

        self.assertRedirects(response, reverse("bookkeeping:income_statement"))
        self.assertEqual(
            BudgetLine.objects.get(account=self.revenue_account, month=1).amount,
            Decimal("10000.00"),
        )
        self.assertEqual(
            BudgetLine.objects.get(account=self.cost_account, month=1).amount,
            Decimal("-500.00"),
        )

    def test_post_updates_existing_line(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=1,
            amount=Decimal("100.00"),
        )

        self.client.post(self._url(), {budget_field_name(self.revenue_account.pk, 1): "250.00"})

        self.assertEqual(
            BudgetLine.objects.get(account=self.revenue_account, month=1).amount,
            Decimal("250.00"),
        )

    def test_post_blank_amount_deletes_existing_line(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=1,
            amount=Decimal("100.00"),
        )

        self.client.post(self._url(), {budget_field_name(self.revenue_account.pk, 1): ""})

        self.assertFalse(BudgetLine.objects.filter(account=self.revenue_account, month=1).exists())

    def test_other_companys_accounting_year_is_not_reachable(self):
        other_company = create_company("Annat budgetbolag AB", "556677-7788")
        other_year = create_accounting_year(other_company)

        response = self.client.get(self._url(other_year))

        self.assertEqual(response.status_code, 404)


class IncomeStatementBudgetContextTests(CompanyTestCase):
    company_name = "Budgetrapportbolaget AB"
    company_org_number = "556677-8899"

    def setUp(self):
        super().setUp()
        self.revenue_account = create_account(self.company, "3041", "Försäljning tjänster", AccountClass.REVENUE)
        self.cost_account = create_account(self.company, "6310", "Försäkringar", AccountClass.OTHER_EXTERNAL_2)
        self.unbudgeted_account = create_account(self.company, "6420", "Telefon", AccountClass.OTHER_EXTERNAL_2)
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

    def _context(self, year=None):
        request = self.client.get(
            reverse("bookkeeping:income_statement"), {"year": (year or self.year).pk}
        ).wsgi_request
        return build_income_statement_context(request, self.company)

    def _post_transaction(self, account, credit=Decimal("0.00"), debit=Decimal("0.00")):
        """Book a two-legged voucher: ``account`` gets the given debit/credit, the bank
        account gets the mirrored other side so the voucher balances."""
        txn = Transaction.objects.create(
            accounting_year=self.year, date="2026-03-15", description="Test", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=account, debit=debit, credit=credit)
        JournalEntry.objects.create(transaction=txn, account=self.bank_account, debit=credit, credit=debit)
        return txn

    def test_row_shows_budget_and_difference_for_populated_account(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=3,
            amount=Decimal("1000.00"),
        )
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=4,
            amount=Decimal("1000.00"),
        )
        self._post_transaction(self.revenue_account, credit=Decimal("2500.00"))

        context = self._context()

        row = next(r for r in context["operating_income_rows"] if r["account"] == self.revenue_account)
        self.assertEqual(row["amount"], Decimal("2500.00"))
        self.assertEqual(row["budget_amount"], Decimal("2000.00"))
        self.assertEqual(row["budget_difference"], Decimal("500.00"))
        self.assertTrue(context["has_budget_data"])

    def test_account_with_budget_but_no_actual_still_shown_with_zero_actual(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.cost_account,
            month=1,
            amount=Decimal("-300.00"),
        )

        context = self._context()

        rows = context["external_cost_rows"]
        row = next(r for r in rows if r["account"] == self.cost_account)
        self.assertEqual(row["amount"], Decimal("0"))
        self.assertEqual(row["budget_amount"], Decimal("-300.00"))
        self.assertEqual(row["budget_difference"], Decimal("300.00"))

    def test_account_with_actual_but_no_budget_shows_zero_budget(self):
        self._post_transaction(self.unbudgeted_account, debit=Decimal("150.00"))

        context = self._context()

        row = next(r for r in context["external_cost_rows"] if r["account"] == self.unbudgeted_account)
        self.assertEqual(row["budget_amount"], Decimal("0"))
        self.assertEqual(row["budget_difference"], row["amount"])

    def test_totals_include_budget_and_diff(self):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=self.revenue_account,
            month=1,
            amount=Decimal("500.00"),
        )
        self._post_transaction(self.revenue_account, credit=Decimal("800.00"))

        context = self._context()

        self.assertEqual(context["total_operating_income_budget"], Decimal("500.00"))
        self.assertEqual(context["total_operating_income_diff"], Decimal("300.00"))
        self.assertEqual(context["annual_result_budget"], Decimal("500.00"))

    def test_has_budget_data_false_when_nothing_budgeted(self):
        self._post_transaction(self.revenue_account, credit=Decimal("800.00"))

        context = self._context()

        self.assertFalse(context["has_budget_data"])
