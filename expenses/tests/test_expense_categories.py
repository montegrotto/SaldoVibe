from decimal import Decimal

from django.urls import reverse

from bookkeeping.models import Transaction
from expenses.models import DEFAULT_EXPENSE_CATEGORIES, ExpenseCategory, ExpenseClaim
from saldovibe.testing import CompanyTestCase, create_company


class ExpenseCategoryTests(CompanyTestCase):
    seed_bas_accounts = True
    company_fields = {"vat_reporting_period": "quarterly"}

    def setUp(self):
        super().setUp()
        ExpenseCategory.seed_defaults_for_company(self.company)
        self.hotel = ExpenseCategory.objects.get(company=self.company, name="Hotell")

    def account(self, number):
        return self.company.accounts.get(number=number)

    def draft(self, **fields):
        return ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Anna Andersson",
            description="Hotell Göteborg",
            expense_date=self.year.start_date,
            category=self.hotel,
            expense_account=self.hotel.account,
            liability_account=self.account("2820"),
            vat_account=self.account("2640"),
            total_amount=Decimal("1120.00"),
            vat_amount=Decimal("120.00"),
            amount_ex_vat=Decimal("1000.00"),
            **fields,
        )

    def test_defaults_are_seeded_once_with_their_bas_accounts(self):
        ExpenseCategory.seed_defaults_for_company(self.company)
        categories = dict(ExpenseCategory.objects.filter(company=self.company).values_list("name", "account__number"))
        self.assertEqual(categories, dict(DEFAULT_EXPENSE_CATEGORIES))

    def test_category_page_adds_renames_and_deletes(self):
        categories = list(ExpenseCategory.objects.filter(company=self.company))
        data = {
            "form-TOTAL_FORMS": str(len(categories) + 1),
            "form-INITIAL_FORMS": str(len(categories)),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        for i, category in enumerate(categories):
            data[f"form-{i}-id"] = category.pk
            data[f"form-{i}-name"] = "Logi" if category == self.hotel else category.name
            data[f"form-{i}-account"] = category.account_id
            if category.name == "Övrigt":
                data[f"form-{i}-DELETE"] = "on"
        new = len(categories)
        data[f"form-{new}-name"] = "Tåg"
        data[f"form-{new}-account"] = self.account("5810").pk

        response = self.client.post(reverse("expenses:category_list"), data)

        self.assertRedirects(response, reverse("expenses:category_list"))
        names = set(ExpenseCategory.objects.filter(company=self.company).values_list("name", flat=True))
        self.assertIn("Logi", names)
        self.assertIn("Tåg", names)
        self.assertNotIn("Hotell", names)
        self.assertNotIn("Övrigt", names)

    def test_category_page_rejects_duplicate_names(self):
        response = self.client.post(
            reverse("expenses:category_list"),
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-name": "hotell",
                "form-0-account": self.account("5831").pk,
            },
        )
        self.assertContains(response, "Det finns redan en kategori med det namnet.")

    def test_web_form_takes_the_account_from_the_category(self):
        response = self.client.post(
            reverse("expenses:expense_create"),
            {
                "person_name": "Anna Andersson",
                "description": "Tankning",
                "expense_date": str(self.year.start_date),
                "category": ExpenseCategory.objects.get(company=self.company, name="Bränsle").pk,
                "total_amount": "500.00",
                "vat_amount": "100.00",
            },
        )
        self.assertRedirects(response, reverse("expenses:expense_list"))
        self.assertEqual(ExpenseClaim.objects.get().expense_account.number, "5611")

    def test_edit_prefills_the_suggested_account_and_books_with_the_chosen_one(self):
        claim = self.draft()
        url = reverse("expenses:expense_edit", args=[claim.pk])

        response = self.client.get(url)
        self.assertContains(response, f'<option value="{self.hotel.account_id}" selected>')

        response = self.client.post(
            url,
            {
                "person_name": "Anna Andersson",
                "description": "Hotell Göteborg",
                "expense_date": str(self.year.start_date),
                "category": self.hotel.pk,
                "expense_account": self.account("5832").pk,
                "total_amount": "1120.00",
                "vat_amount": "120.00",
                "register": "1",
            },
        )

        self.assertRedirects(response, reverse("expenses:expense_detail", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertTrue(claim.is_registered)
        rows = claim.registered_transaction.entries.values_list("account__number", "debit")
        self.assertIn(("5832", Decimal("1000.00")), list(rows))

    def test_booked_claims_cannot_be_edited(self):
        claim = self.draft()
        claim.register_and_bookkeep(self.user)
        response = self.client.post(reverse("expenses:expense_edit", args=[claim.pk]), {"description": "Ändrad"})
        self.assertRedirects(response, reverse("expenses:expense_detail", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.description, "Hotell Göteborg")
        self.assertEqual(Transaction.objects.count(), 1)

    def test_other_companies_categories_are_not_offered(self):
        other = create_company("Annat AB", "556000-0001")
        ExpenseCategory.objects.create(company=other, name="Främmande", account=self.account("6110"))
        response = self.client.get(reverse("expenses:expense_create"))
        self.assertContains(response, "Hotell")
        self.assertNotContains(response, "Främmande")
