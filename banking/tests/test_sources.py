from django.db import IntegrityError
from django.urls import reverse

from banking.models import BankAccount, BankTransaction
from banking.services import ensure_tax_account_for_company
from banking.tests.base import BankingTestCase


class BankSourceTests(BankingTestCase):
    def test_can_create_manual_bank_transaction(self):
        response = self.client.post(
            reverse("banking:create_manual_transaction"),
            {
                "bank_account": self.bank_source.pk,
                "date": "2026-06-25",
                "description": "Manuell justering",
                "amount": "-250.00",
                "balance": "9750.00",
                "external_id": "",
            },
        )

        self.assertRedirects(response, self._transaction_list_url_for_source())
        tx = BankTransaction.objects.get(company=self.company, description="Manuell justering")
        self.assertEqual(str(tx.amount), "-250.00")
        self.assertIsNone(tx.import_batch)
        self.assertTrue(tx.external_id.startswith("manual-2026-06-25-"))

    def test_can_delete_unbooked_transaction(self):
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-26",
            description="Ska tas bort",
            amount="-100.00",
            external_id="tx-delete-1",
        )

        response = self.client.post(reverse("banking:delete_transaction", args=[bank_tx.pk]))

        self.assertRedirects(response, self._transaction_list_url_for_source())
        self.assertFalse(BankTransaction.objects.filter(pk=bank_tx.pk).exists())

    def test_cannot_delete_booked_transaction(self):
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-27",
            description="Bokförd rad",
            amount="500.00",
            external_id="tx-delete-2",
            is_booked=True,
        )

        response = self.client.post(reverse("banking:delete_transaction", args=[bank_tx.pk]))

        self.assertRedirects(response, self._transaction_list_url_for_source())
        self.assertTrue(BankTransaction.objects.filter(pk=bank_tx.pk).exists())

    def test_can_create_bank_source_from_account_list_post(self):
        response = self.client.post(
            reverse("banking:account_create"),
            {
                "name": "Savings Account",
                "account_number": "5555-6666",
                "account_type": "savings",
                "default_bank_profile": "nordea",
                "bookkeeping_account": self.bank_gl_account.pk,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("banking:account_list"))
        created = BankAccount.objects.get(company=self.company, name="Savings Account")
        self.assertEqual(created.default_bank_profile, "nordea")

    def test_tax_account_is_auto_created_for_company(self):
        ensure_tax_account_for_company(self.company)
        tax_account = BankAccount.objects.get(company=self.company, account_type="tax")
        self.assertEqual(tax_account.bookkeeping_account.number, "1630")
        self.assertEqual(tax_account.default_bank_profile, "skatteverket")

    def test_can_edit_existing_bank_source(self):
        response = self.client.post(
            reverse("banking:account_update", args=[self.bank_source.pk]),
            {
                "name": "Main Bank Updated",
                "account_number": "1111-9999",
                "account_type": "bank",
                "default_bank_profile": "swedbank",
                "bookkeeping_account": self.bank_gl_account.pk,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("banking:account_list"))
        self.bank_source.refresh_from_db()
        self.assertEqual(self.bank_source.name, "Main Bank Updated")
        self.assertEqual(self.bank_source.account_number, "1111-9999")
        self.assertEqual(self.bank_source.default_bank_profile, "swedbank")

    def test_tax_account_cannot_be_edited_from_bank_sources(self):
        ensure_tax_account_for_company(self.company)
        tax_account = BankAccount.objects.get(company=self.company, account_type="tax")

        response = self.client.post(
            reverse("banking:account_update", args=[tax_account.pk]),
            {
                "name": "Skattekonto ändrat",
                "account_number": "9999",
                "account_type": "tax",
                "default_bank_profile": "generic",
                "bookkeeping_account": self.bank_gl_account.pk,
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("banking:account_list"))
        tax_account.refresh_from_db()
        self.assertEqual(tax_account.name, "Skattekonto")
        self.assertEqual(tax_account.bookkeeping_account.number, "1630")

    def test_cannot_create_tax_account_source_manually(self):
        response = self.client.post(
            reverse("banking:account_create"),
            {
                "name": "Skattekonto",
                "account_number": "1630",
                "account_type": "tax",
                "default_bank_profile": "skatteverket",
                "bookkeeping_account": self.tax_gl_account.pk,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(BankAccount.objects.filter(company=self.company, account_type="tax").count(), 1)

    def test_cannot_have_multiple_tax_accounts(self):
        ensure_tax_account_for_company(self.company)
        with self.assertRaises(IntegrityError):
            BankAccount.objects.create(
                company=self.company,
                name="Skattekonto extra",
                account_number="1630-2",
                account_type="tax",
                default_bank_profile="skatteverket",
                bookkeeping_account=self.tax_gl_account,
                is_active=True,
            )
