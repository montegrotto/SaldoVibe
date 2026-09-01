from decimal import Decimal

from django.urls import reverse

from banking.models import BankTransaction
from banking.tests.base import BankingTestCase
from bookkeeping.models import Account, AccountClass


class ManualBookingFormAccountIdRenderingTests(BankingTestCase):
    """USE_THOUSAND_SEPARATOR is on, so a raw `{{ account.pk }}` in a template renders
    with a locale thousands separator (e.g. "1\xa0930") for any account whose primary
    key is >= 1000. That corrupts the id wherever it's used for exact JS/POST matching
    (the counter-account <option value>, the locked bank row's data-account-id) since
    the account_balances lookup dict and POST parsing expect a plain digit string.
    """

    def setUp(self):
        super().setUp()
        # Force a pk >= 1000 regardless of however many rows earlier tests created.
        self.high_pk_account = Account.objects.create(
            id=123456,
            company=self.company,
            number="6110",
            name="Kontorsmaterial",
            account_class=AccountClass.OTHER_EXTERNAL,
            is_active=True,
        )
        self.bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-07-05",
            description="Test",
            amount=Decimal("500.00"),
            external_id="render-test-1",
        )

    def test_counter_account_option_value_has_no_thousand_separator(self):
        response = self.client.get(reverse("banking:book_transaction", args=[self.bank_tx.pk]))
        html = response.content.decode()
        self.assertIn(f'<option value="{self.high_pk_account.pk}">', html)
        self.assertNotIn("123\xa0456", html)

    def test_locked_row_account_id_has_no_thousand_separator_when_bank_account_id_is_high(self):
        high_pk_bank_gl = Account.objects.create(
            id=654321,
            company=self.company,
            number="1940",
            name="Extra bankkonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )
        from banking.models import BankAccount

        bank_source_2 = BankAccount.objects.create(
            company=self.company,
            name="Second Bank",
            account_number="3333-4444",
            account_type="bank",
            bookkeeping_account=high_pk_bank_gl,
            is_active=True,
        )
        bank_tx_2 = BankTransaction.objects.create(
            company=self.company,
            bank_account=bank_source_2,
            date="2026-07-05",
            description="Test 2",
            amount=Decimal("500.00"),
            external_id="render-test-2",
        )

        response = self.client.get(reverse("banking:book_transaction", args=[bank_tx_2.pk]))
        html = response.content.decode()
        self.assertIn(f'data-account-id="{high_pk_bank_gl.pk}"', html)
        self.assertNotIn("654\xa0321", html)
