from datetime import date
from decimal import Decimal

from django.urls import reverse

from bookkeeping.models import AccountClass, BudgetLine, JournalEntry, Transaction
from bookkeeping.reports import build_income_forecast_context
from saldovibe.testing import CompanyTestCase, create_account, create_accounting_year, create_company


class IncomeForecastContextTests(CompanyTestCase):
    company_name = "Prognosbolaget AB"
    company_org_number = "556677-9911"

    def setUp(self):
        super().setUp()
        self.revenue_account = create_account(self.company, "3041", "Försäljning tjänster", AccountClass.REVENUE)
        self.rent_account = create_account(self.company, "5010", "Lokalhyra", AccountClass.OTHER_EXTERNAL)
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

    def _url(self):
        return reverse("bookkeeping:income_forecast")

    def _context(self, **params):
        params.setdefault("year", self.year.pk)
        request = self.client.get(self._url(), params).wsgi_request
        return build_income_forecast_context(request, self.company)

    def _post_transaction(self, account, transaction_date, credit=Decimal("0.00"), debit=Decimal("0.00")):
        txn = Transaction.objects.create(
            accounting_year=self.year, date=transaction_date, description="Test", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=account, debit=debit, credit=credit)
        JournalEntry.objects.create(transaction=txn, account=self.bank_account, debit=credit, credit=debit)

    def _budget(self, account, month, amount):
        BudgetLine.objects.create(
            company=self.company,
            accounting_year=self.year,
            account=account,
            month=month,
            amount=Decimal(amount),
        )

    def _section(self, context, key):
        return next(section for section in context["sections"] if section["key"] == key)

    def test_without_bookkeeping_there_is_nothing_to_forecast_from(self):
        context = self._context()

        self.assertEqual(context["month_choices"], [])
        self.assertIsNone(context["selected_month"])
        self.assertEqual(context["sections"], [])

    def test_months_with_bookkeeping_are_selectable_and_latest_is_default(self):
        self._post_transaction(self.revenue_account, "2026-01-15", credit=Decimal("800.00"))
        self._post_transaction(self.revenue_account, "2026-03-15", credit=Decimal("700.00"))

        context = self._context()

        self.assertEqual([choice["value"] for choice in context["month_choices"]], ["2026-01", "2026-03"])
        self.assertEqual([choice["label"] for choice in context["month_choices"]], ["Jan 2026", "Mar 2026"])
        self.assertEqual(context["selected_month"], "2026-03")
        self.assertEqual(context["cutoff_date"], date(2026, 3, 31))

    def test_actual_stops_at_the_cutoff_and_rest_is_prefilled_from_the_budget(self):
        self._post_transaction(self.revenue_account, "2026-01-15", credit=Decimal("800.00"))
        self._post_transaction(self.revenue_account, "2026-02-15", credit=Decimal("700.00"))
        for month in range(1, 13):
            self._budget(self.revenue_account, month, "1000.00")

        context = self._context(month="2026-01")

        row = self._section(context, "operating_income")["rows"][0]
        self.assertEqual(row["account"], self.revenue_account)
        self.assertEqual(row["actual"], Decimal("800.00"))
        self.assertEqual(row["rest"], Decimal("11000.00"))
        self.assertEqual(row["total"], Decimal("11800.00"))
        self.assertTrue(context["budget_prefilled"])

    def test_cutoff_in_the_querystring_that_has_no_bookkeeping_falls_back_to_the_default(self):
        self._post_transaction(self.revenue_account, "2026-01-15", credit=Decimal("800.00"))

        self.assertEqual(self._context(month="2026-06")["selected_month"], "2026-01")
        self.assertEqual(self._context(month="skräp")["selected_month"], "2026-01")

    def test_totals_add_the_rest_of_the_year_to_the_actual(self):
        self._post_transaction(self.revenue_account, "2026-01-15", credit=Decimal("40000.00"))
        self._post_transaction(self.rent_account, "2026-01-15", debit=Decimal("8000.00"))
        for month in range(1, 13):
            self._budget(self.revenue_account, month, "45000.00")
            self._budget(self.rent_account, month, "-8000.00")

        context = self._context()

        income = self._section(context, "operating_income")
        self.assertEqual(income["actual"], Decimal("40000.00"))
        self.assertEqual(income["rest"], Decimal("495000.00"))
        costs = self._section(context, "external_costs")
        self.assertEqual(costs["actual"], Decimal("-8000.00"))
        self.assertEqual(costs["rest"], Decimal("-88000.00"))
        self.assertEqual(context["operating_result"]["total"], Decimal("439000.00"))
        self.assertEqual(context["preliminary_result"]["total"], Decimal("439000.00"))

    def test_account_booked_only_after_the_cutoff_is_still_listed_with_zero_actual(self):
        self._post_transaction(self.revenue_account, "2026-01-15", credit=Decimal("800.00"))
        self._post_transaction(self.rent_account, "2026-03-15", debit=Decimal("8000.00"))

        context = self._context(month="2026-01")

        row = self._section(context, "external_costs")["rows"][0]
        self.assertEqual(row["account"], self.rent_account)
        self.assertEqual(row["actual"], Decimal("0"))
        self.assertEqual(row["total"], Decimal("0"))

    def test_broken_fiscal_year_splits_the_months_in_fiscal_order(self):
        broken_company = create_company("Brutet år AB", "556677-9922")
        broken_company.users.add(self.user)
        broken_year = create_accounting_year(broken_company, start_date="2026-05-01", end_date="2027-04-30")
        revenue = create_account(broken_company, "3041", "Försäljning", AccountClass.REVENUE)
        bank = create_account(broken_company, "1930", "Företagskonto", AccountClass.ASSET)
        txn = Transaction.objects.create(
            accounting_year=broken_year, date="2026-06-15", description="Test", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=revenue, credit=Decimal("500.00"))
        JournalEntry.objects.create(transaction=txn, account=bank, debit=Decimal("500.00"))
        # Maj ligger före brytmånaden juni, april efter - trots att 4 < 6 som månadsnummer.
        BudgetLine.objects.create(
            company=broken_company, accounting_year=broken_year, account=revenue, month=5, amount=Decimal("100.00")
        )
        BudgetLine.objects.create(
            company=broken_company, accounting_year=broken_year, account=revenue, month=4, amount=Decimal("200.00")
        )

        request = self.client.get(self._url(), {"year": broken_year.pk}).wsgi_request
        context = build_income_forecast_context(request, broken_company)

        self.assertEqual(context["selected_month"], "2026-06")
        row = self._section(context, "operating_income")["rows"][0]
        self.assertEqual(row["actual"], Decimal("500.00"))
        self.assertEqual(row["rest"], Decimal("200.00"))


class IncomeForecastViewTests(CompanyTestCase):
    company_name = "Prognosvyn AB"
    company_org_number = "556677-9933"

    def setUp(self):
        super().setUp()
        self.revenue_account = create_account(self.company, "3041", "Försäljning tjänster", AccountClass.REVENUE)
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

    def test_page_renders_an_editable_rest_of_year_field_per_account(self):
        txn = Transaction.objects.create(
            accounting_year=self.year, date="2026-02-15", description="Test", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=self.revenue_account, credit=Decimal("800.00"))
        JournalEntry.objects.create(transaction=txn, account=self.bank_account, debit=Decimal("800.00"))

        response = self.client.get(reverse("bookkeeping:income_forecast"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Försäljning tjänster")
        self.assertContains(response, "forecast-rest")
        self.assertContains(response, "Preliminärt resultat")

    def test_page_without_bookkeeping_explains_why_there_is_no_forecast(self):
        response = self.client.get(reverse("bookkeeping:income_forecast"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ingen bokföring")
        self.assertNotContains(response, "forecast-rest")
