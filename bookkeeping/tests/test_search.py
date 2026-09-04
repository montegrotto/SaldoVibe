from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from bookkeeping.models import AccountClass, JournalEntry, Transaction
from bookkeeping.views.search import parse_amount
from invoicing.models import Customer
from saldovibe.testing import (
    create_account,
    create_accounting_year,
    create_company,
    create_user,
    set_active_company,
)


class SearchTests(TestCase):
    def setUp(self):
        self.user = create_user("sok@example.com")
        self.company = create_company("Sökbolaget AB", users=[self.user])
        year = create_accounting_year(self.company)
        bank = create_account(self.company, "1930", "Bank", AccountClass.ASSET)
        sales = create_account(self.company, "3001", "Försäljning", AccountClass.REVENUE)
        self.txn = Transaction.objects.create(
            accounting_year=year,
            date=date(2026, 3, 5),
            description="Hyra mars",
            voucher_series="A",
            voucher_number=12,
        )
        JournalEntry.objects.create(transaction=self.txn, account=bank, debit=Decimal("1234.50"))
        JournalEntry.objects.create(transaction=self.txn, account=sales, credit=Decimal("1234.50"))
        Customer.objects.create(company=self.company, name="Kalles Kunder AB")

        other_user = create_user("annan@example.com")
        other = create_company("Annat bolag AB", users=[other_user])
        Customer.objects.create(company=other, name="Hyra Hemligt AB")

        self.client.force_login(self.user)
        set_active_company(self.client, self.company)

    def _search(self, q):
        return self.client.get(reverse("bookkeeping:search"), {"q": q})

    def test_parse_amount(self):
        self.assertEqual(parse_amount("1 234,50"), Decimal("1234.50"))
        self.assertIsNone(parse_amount("hyra"))

    def test_text_hit_is_company_scoped(self):
        response = self._search("hyra")
        self.assertContains(response, "Hyra mars")
        self.assertNotContains(response, "Hyra Hemligt")

    def test_amount_and_voucher_hits(self):
        self.assertContains(self._search("1234,50"), "Hyra mars")
        self.assertContains(self._search("a12"), "Hyra mars")
        self.assertContains(self._search("kalles"), "Kalles Kunder AB")

    def test_no_hits_and_empty_query(self):
        self.assertContains(self._search("finnsinte"), "Inga träffar")
        self.assertContains(self.client.get(reverse("bookkeeping:search")), "Skriv något i sökfältet")
