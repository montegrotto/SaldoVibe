from django.urls import reverse

from banking.models import BankAccount
from bookkeeping.models import Account, AccountClass
from saldovibe.testing import CompanyTestCase, create_accounts


class BankingTestCase(CompanyTestCase):
    user_email = "banking-user@example.com"
    company_name = "Banking AB"
    company_org_number = "556677-9988"

    def setUp(self):
        super().setUp()

        accounts = create_accounts(
            self.company,
            [
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("3010", "Försäljning", AccountClass.REVENUE),
                ("1510", "Kundfordringar", AccountClass.ASSET),
                ("3740", "Öres- och kronutjämning", AccountClass.REVENUE),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
                ("2910", "Upplupna löner", AccountClass.EQUITY_LIABILITY),
            ],
        )
        self.bank_gl_account = accounts["1930"]
        self.counter_account = accounts["3010"]
        self.receivable_account = accounts["1510"]
        self.rounding_account = accounts["3740"]
        self.payable_account = accounts["2440"]
        self.salary_liability_account = accounts["2910"]

        # 1630 is not ours to create: banking's post_save on Company already made it
        # (together with the matching skattekonto BankAccount) when the company was.
        self.tax_gl_account, _ = Account.objects.get_or_create(
            company=self.company,
            number="1630",
            defaults={
                "name": "Skattekonto",
                "account_class": AccountClass.ASSET,
                "is_active": True,
            },
        )

        self.bank_source = BankAccount.objects.create(
            company=self.company,
            name="Main Bank",
            account_number="1111-2222",
            account_type="bank",
            bookkeeping_account=self.bank_gl_account,
            is_active=True,
        )

    def _transaction_list_url_for_source(self):
        return f"{reverse('banking:transaction_list')}?bank_account={self.bank_source.pk}"
