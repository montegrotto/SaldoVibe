from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from bookkeeping.models import AccountClass, PeriodLock, Transaction
from bookkeeping.periodization import create_periodization_vouchers, split_amount, voucher_dates_for_months
from saldovibe.testing import CompanyTestCase, create_account


class VoucherDatesAndSplitTests(CompanyTestCase):
    """Pure helpers - no DB needed beyond CompanyTestCase's baseline, but they're
    cheap enough to just piggyback on it rather than a plain TestCase."""

    def test_voucher_dates_walk_calendar_months_across_year_boundary(self):
        dates = voucher_dates_for_months(date(2026, 11, 15), 4)
        self.assertEqual(
            dates,
            [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)],
        )

    def test_split_amount_puts_rounding_remainder_in_last_period(self):
        amounts = split_amount(Decimal("1000.00"), 3)
        self.assertEqual(amounts, [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")])
        self.assertEqual(sum(amounts), Decimal("1000.00"))


class CreatePeriodizationVouchersTests(CompanyTestCase):
    company_name = "Periodiseringsbolaget AB"
    company_org_number = "556677-3344"
    accounting_year_dates = (date(2026, 1, 1), date(2026, 12, 31))

    def setUp(self):
        super().setUp()
        self.insurance_cost = create_account(self.company, "6310", "Försäkringar", AccountClass.OTHER_EXTERNAL_2)
        self.prepaid_expense = create_account(
            self.company, "1730", "Förutbetalda försäkringspremier", AccountClass.ASSET
        )
        self.rent_revenue = create_account(self.company, "3011", "Hyresintäkter", AccountClass.REVENUE)
        self.deferred_revenue = create_account(
            self.company, "2970", "Förutbetalda hyresintäkter", AccountClass.EQUITY_LIABILITY
        )

    def test_cost_spread_debits_cost_account_and_credits_prepaid_account(self):
        created = create_periodization_vouchers(
            self.company,
            self.user,
            description="Försäkring 2026",
            amount=Decimal("1200.00"),
            account=self.insurance_cost,
            counter_account=self.prepaid_expense,
            months=3,
            start_date=date(2026, 1, 1),
        )

        self.assertEqual(len(created), 3)
        self.assertEqual(
            [txn.date for txn in created],
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )
        for index, txn in enumerate(created, start=1):
            self.assertTrue(txn.is_balanced)
            self.assertEqual(txn.description, f"Försäkring 2026 ({index}/3)")
            cost_entry = txn.entries.get(account=self.insurance_cost)
            prepaid_entry = txn.entries.get(account=self.prepaid_expense)
            self.assertEqual(cost_entry.debit, Decimal("400.00"))
            self.assertEqual(cost_entry.credit, Decimal("0.00"))
            self.assertEqual(prepaid_entry.credit, Decimal("400.00"))
            self.assertEqual(prepaid_entry.debit, Decimal("0.00"))

    def test_revenue_spread_debits_deferred_account_and_credits_revenue_account(self):
        created = create_periodization_vouchers(
            self.company,
            self.user,
            description="Förhyrning",
            amount=Decimal("900.00"),
            account=self.rent_revenue,
            counter_account=self.deferred_revenue,
            months=3,
            start_date=date(2026, 1, 1),
        )

        self.assertEqual(len(created), 3)
        for txn in created:
            revenue_entry = txn.entries.get(account=self.rent_revenue)
            deferred_entry = txn.entries.get(account=self.deferred_revenue)
            self.assertEqual(revenue_entry.credit, Decimal("300.00"))
            self.assertEqual(deferred_entry.debit, Decimal("300.00"))

    def test_locked_period_among_the_n_dates_blocks_all_creation(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            reason="Momsavstämd",
        )

        with self.assertRaises(ValidationError) as ctx:
            create_periodization_vouchers(
                self.company,
                self.user,
                description="Försäkring 2026",
                amount=Decimal("1200.00"),
                account=self.insurance_cost,
                counter_account=self.prepaid_expense,
                months=3,
                start_date=date(2026, 1, 1),
            )

        self.assertIn("2026-02", ctx.exception.messages[0])
        self.assertEqual(Transaction.objects.filter(accounting_year__company=self.company).count(), 0)

    def test_date_outside_any_accounting_year_blocks_all_creation(self):
        with self.assertRaises(ValidationError) as ctx:
            create_periodization_vouchers(
                self.company,
                self.user,
                description="Försäkring",
                amount=Decimal("600.00"),
                account=self.insurance_cost,
                counter_account=self.prepaid_expense,
                months=3,
                start_date=date(2026, 11, 1),
            )

        self.assertTrue(any("räkenskapsår" in message for message in ctx.exception.messages))
        self.assertEqual(Transaction.objects.filter(accounting_year__company=self.company).count(), 0)


class PeriodizationViewTests(CompanyTestCase):
    company_name = "Periodiseringsvyn AB"
    company_org_number = "556677-4455"
    accounting_year_dates = (date(2026, 1, 1), date(2026, 12, 31))

    def setUp(self):
        super().setUp()
        self.insurance_cost = create_account(self.company, "6310", "Försäkringar", AccountClass.OTHER_EXTERNAL_2)
        self.prepaid_expense = create_account(
            self.company, "1730", "Förutbetalda försäkringspremier", AccountClass.ASSET
        )
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.accrued_liability = create_account(
            self.company, "2990", "Övriga upplupna kostnader", AccountClass.EQUITY_LIABILITY
        )

    def _post(self, **overrides):
        data = {
            "description": "Försäkring 2026",
            "amount": "1200.00",
            "account": str(self.insurance_cost.pk),
            "counter_account": str(self.prepaid_expense.pk),
            "months": "3",
            "start_date": "2026-01-01",
        }
        data.update(overrides)
        return self.client.post(reverse("bookkeeping:periodization_create"), data)

    def test_get_renders_form(self):
        response = self.client.get(reverse("bookkeeping:periodization_create"))
        self.assertEqual(response.status_code, 200)

    def test_post_creates_n_vouchers_and_redirects(self):
        response = self._post()

        self.assertRedirects(response, reverse("bookkeeping:transaction_list"))
        self.assertEqual(
            Transaction.objects.filter(accounting_year__company=self.company).count(),
            3,
        )

    def test_post_rejects_mismatched_account_and_counter_account(self):
        # 2990 is a valid 29xx account, but self.insurance_cost is a cost account (not
        # revenue), so it must be periodized against a 17xx account instead.
        response = self._post(counter_account=str(self.accrued_liability.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "17xx-konto")
        self.assertEqual(Transaction.objects.filter(accounting_year__company=self.company).count(), 0)

    def test_post_rejects_counter_account_outside_17xx_29xx(self):
        response = self._post(counter_account=str(self.bank_account.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Välj ett giltigt alternativ")
        self.assertEqual(Transaction.objects.filter(accounting_year__company=self.company).count(), 0)

    def test_post_with_locked_period_shows_error_and_creates_nothing(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            reason="Momsavstämd",
        )

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "är låst")
        self.assertEqual(Transaction.objects.filter(accounting_year__company=self.company).count(), 0)
