from datetime import date

from django.urls import reverse

from bookkeeping.models import Account, AccountClass, AccountingYear, JournalEntry, Transaction
from saldovibe.testing import CompanyTestCase, create_user, set_active_company


class AccountingYearCreateRuleTests(CompanyTestCase):
    user_email = "year-rule@example.com"
    user_fields = {"is_staff": True}
    company_name = "Year Rule AB"
    company_org_number = "191919-1919"
    # Deliberately starts with no accounting year: the first-year create form is
    # what half of these tests assert on.
    accounting_year_dates = None

    def test_create_form_has_no_start_date_default_for_first_accounting_year(self):
        response = self.client.get(reverse("bookkeeping:accounting_year_create"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].fields["start_date"].initial, None)
        self.assertEqual(response.context["form"].fields["end_date"].initial, None)

    def test_create_form_prefills_start_date_day_after_previous_year_end(self):
        AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        response = self.client.get(reverse("bookkeeping:accounting_year_create"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].fields["start_date"].initial,
            date(2027, 1, 1),
        )
        self.assertEqual(
            response.context["form"].fields["end_date"].initial,
            date(2027, 12, 31),
        )
        self.assertTrue(response.context["form"].fields["start_date"].disabled)

    def test_create_ignores_posted_start_date_when_field_is_locked(self):
        AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        response = self.client.post(
            reverse("bookkeeping:accounting_year_create"),
            {
                "start_date": "2027-01-02",
                "end_date": "2027-06-30",
            },
        )

        self.assertEqual(response.status_code, 302)
        year_2027 = AccountingYear.objects.get(company=self.company, end_date="2027-06-30")
        self.assertEqual(year_2027.start_date, date(2027, 1, 1))

    def test_existing_accounting_year_cannot_be_edited(self):
        year = AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        get_response = self.client.get(reverse("bookkeeping:accounting_year_update", args=[year.pk]), follow=True)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "kan inte ändras")

        post_response = self.client.post(
            reverse("bookkeeping:accounting_year_update", args=[year.pk]),
            {"start_date": "2026-01-01", "end_date": "2026-11-30"},
            follow=True,
        )
        self.assertEqual(post_response.status_code, 200)
        year.refresh_from_db()
        self.assertEqual(year.end_date, date(2026, 12, 31))

    def test_delete_is_blocked_when_it_would_create_gap_between_years(self):
        year_2025 = AccountingYear.objects.create(
            company=self.company,
            start_date="2025-01-01",
            end_date="2025-12-31",
        )
        year_2026 = AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        AccountingYear.objects.create(
            company=self.company,
            start_date="2027-02-01",
            end_date="2027-12-31",
        )

        response = self.client.post(
            reverse("bookkeeping:accounting_year_delete", args=[year_2026.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "skulle skapa ett glapp")
        self.assertTrue(AccountingYear.objects.filter(pk=year_2026.pk).exists())

    def test_delete_is_allowed_when_no_gap_is_created(self):
        AccountingYear.objects.create(
            company=self.company,
            start_date="2025-01-01",
            end_date="2025-12-31",
        )
        year_2026 = AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        AccountingYear.objects.create(
            company=self.company,
            start_date="2026-12-15",
            end_date="2027-12-31",
        )

        response = self.client.post(
            reverse("bookkeeping:accounting_year_delete", args=[year_2026.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "har tagits bort")
        self.assertFalse(AccountingYear.objects.filter(pk=year_2026.pk).exists())

    def test_accounting_year_delete_is_blocked_for_non_finance_admin(self):
        non_staff = create_user("year-delete-nonstaff@example.com", is_staff=False)
        self.company.users.add(non_staff)
        year = AccountingYear.objects.create(
            company=self.company,
            start_date="2028-01-01",
            end_date="2028-12-31",
        )

        set_active_company(self.client, self.company)
        self.client.force_login(non_staff)

        response = self.client.post(reverse("bookkeeping:accounting_year_delete", args=[year.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))
        self.assertTrue(AccountingYear.objects.filter(pk=year.pk).exists())

    def test_delete_confirmation_shows_number_of_affected_transactions(self):
        year = AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        debit_account = Account.objects.create(
            company=self.company,
            number="1930",
            name="Företagskonto",
            account_class=AccountClass.ASSET,
        )
        credit_account = Account.objects.create(
            company=self.company,
            number="2440",
            name="Leverantörsskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
        )

        txn_1 = Transaction.objects.create(
            accounting_year=year,
            date="2026-01-10",
            description="Test 1",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn_1, account=debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn_1, account=credit_account, debit="0.00", credit="100.00")

        txn_2 = Transaction.objects.create(
            accounting_year=year,
            date="2026-01-11",
            description="Test 2",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn_2, account=debit_account, debit="200.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn_2, account=credit_account, debit="0.00", credit="200.00")

        response = self.client.get(reverse("bookkeeping:accounting_year_delete", args=[year.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Antal verifikationer i räkenskapsåret")
        self.assertContains(response, ">2<")

    def test_delete_is_blocked_when_accounting_year_contains_transactions(self):
        year = AccountingYear.objects.create(
            company=self.company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        debit_account = Account.objects.create(
            company=self.company,
            number="1930",
            name="Företagskonto",
            account_class=AccountClass.ASSET,
        )
        credit_account = Account.objects.create(
            company=self.company,
            number="2440",
            name="Leverantörsskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
        )

        txn = Transaction.objects.create(
            accounting_year=year,
            date="2026-02-01",
            description="To delete",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=debit_account, debit="300.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=credit_account, debit="0.00", credit="300.00")

        response = self.client.post(
            reverse("bookkeeping:accounting_year_delete", args=[year.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:accounting_year_list"))
        self.assertTrue(Transaction.objects.filter(pk=txn.pk).exists())
        self.assertTrue(AccountingYear.objects.filter(pk=year.pk).exists())
