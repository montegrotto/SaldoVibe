from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from bookkeeping.models import AccountClass, JournalEntry
from bookkeeping.payables import register_manual_payment
from expenses.models import ExpenseClaim, ExpenseClaimPayment
from saldovibe.testing import CompanyTestCase, create_accounts


class ExpenseClaimTestCase(CompanyTestCase):
    user_email = "expenses-user@example.com"
    company_name = "Utlägg AB"
    company_org_number = "556611-2233"

    def setUp(self):
        super().setUp()
        accounts = create_accounts(
            self.company,
            [
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("6110", "Kontorsmateriel", AccountClass.OTHER_EXTERNAL),
                ("2640", "Ingående moms", AccountClass.EQUITY_LIABILITY),
                ("2820", "Kortfristiga skulder till anställda", AccountClass.EQUITY_LIABILITY),
                ("3740", "Öres- och kronutjämning", AccountClass.REVENUE),
            ],
        )
        self.bank_account = accounts["1930"]
        self.expense_account = accounts["6110"]
        self.vat_account = accounts["2640"]
        self.liability_account = accounts["2820"]
        self.rounding_account = accounts["3740"]

    def _create_claim(self, *, total=Decimal("1250.00"), vat=Decimal("250.00")):
        return ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Mattias Utläggare",
            description="Kontorsmateriel",
            expense_date="2026-07-05",
            expense_account=self.expense_account,
            liability_account=self.liability_account,
            vat_account=self.vat_account if vat > 0 else None,
            amount_ex_vat=total - vat,
            vat_amount=vat,
            total_amount=total,
        )


class ExpenseClaimRegistrationTests(ExpenseClaimTestCase):
    def test_register_and_bookkeep_creates_balanced_transaction(self):
        claim = self._create_claim()

        txn = claim.register_and_bookkeep(self.user)

        claim.refresh_from_db()
        self.assertTrue(claim.is_registered)
        self.assertEqual(claim.registered_transaction_id, txn.id)

        expense_entry = JournalEntry.objects.get(transaction=txn, account=self.expense_account)
        self.assertEqual(expense_entry.debit, Decimal("1000.00"))
        vat_entry = JournalEntry.objects.get(transaction=txn, account=self.vat_account)
        self.assertEqual(vat_entry.debit, Decimal("250.00"))
        liability_entry = JournalEntry.objects.get(transaction=txn, account=self.liability_account)
        self.assertEqual(liability_entry.credit, Decimal("1250.00"))

    def test_create_view_saves_and_registers(self):
        response = self.client.post(
            reverse("expenses:expense_create"),
            {
                "person_name": "Extern Person",
                "description": "Tågbiljett",
                "expense_date": "2026-07-05",
                "expense_account": str(self.expense_account.pk),
                "total_amount": "500.00",
                "vat_amount": "0.00",
                "register": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        claim = ExpenseClaim.objects.get(company=self.company, description="Tågbiljett")
        self.assertTrue(claim.is_registered)
        self.assertEqual(claim.liability_account, self.liability_account)
        self.assertEqual(claim.amount_ex_vat, Decimal("500.00"))


class ExpenseClaimManualPaymentTests(ExpenseClaimTestCase):
    def test_register_manual_payment_posts_verification(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)

        txn = register_manual_payment(
            claim,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1250.00"),
            payment_account=self.bank_account,
        )
        claim.refresh_from_db()

        self.assertTrue(claim.is_paid)
        self.assertEqual(claim.paid_amount, Decimal("1250.00"))
        self.assertEqual(claim.payment_date, date(2026, 7, 10))
        self.assertEqual(claim.payment_account, self.bank_account)
        self.assertEqual(claim.payment_transaction_id, txn.id)
        self.assertEqual(ExpenseClaimPayment.objects.filter(payable=claim).count(), 1)

        debits = {entry.account.number: entry.debit for entry in txn.entries.filter(debit__gt=0)}
        credits = {entry.account.number: entry.credit for entry in txn.entries.filter(credit__gt=0)}
        self.assertEqual(debits, {"2820": Decimal("1250.00")})
        self.assertEqual(credits, {"1930": Decimal("1250.00")})

    def test_register_manual_payment_rejects_already_paid_claim(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)
        register_manual_payment(
            claim,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1250.00"),
            payment_account=self.bank_account,
        )
        claim.refresh_from_db()

        with self.assertRaises(ValidationError):
            register_manual_payment(
                claim,
                self.user,
                payment_date=date(2026, 7, 11),
                amount=Decimal("1.00"),
                payment_account=self.bank_account,
            )

    def test_register_manual_payment_rejects_overpayment(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)

        with self.assertRaises(ValidationError):
            register_manual_payment(
                claim,
                self.user,
                payment_date=date(2026, 7, 10),
                amount=Decimal("2000.00"),
                payment_account=self.bank_account,
            )

    def test_register_manual_payment_with_write_off_settles_claim(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)

        txn = register_manual_payment(
            claim,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("1249.50"),
            payment_account=self.bank_account,
            write_off_amount=Decimal("0.50"),
            write_off_account=self.rounding_account,
        )
        claim.refresh_from_db()

        self.assertTrue(claim.is_paid)
        rounding_entry = txn.entries.get(account=self.rounding_account)
        self.assertEqual(rounding_entry.credit, Decimal("0.50"))

    def test_unmark_manually_paid_rejects_bank_paid_claim(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)
        claim.is_paid = True
        claim.payment_transaction = claim.registered_transaction
        claim.save(update_fields=["is_paid", "payment_transaction"])

        with self.assertRaises(ValidationError):
            claim.unmark_manually_paid(self.user)

    def test_register_payment_view(self):
        claim = self._create_claim()
        claim.register_and_bookkeep(self.user)

        response = self.client.post(
            reverse("expenses:expense_register_payment", args=[claim.pk]),
            {
                "payment_date": "2026-07-10",
                "amount": "1250.00",
                "payment_account": str(self.bank_account.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        self.assertTrue(claim.is_paid)
        self.assertEqual(claim.paid_amount, claim.total_amount)
        self.assertIsNotNone(claim.payment_transaction)


class ExpenseClaimDoubleBookingTests(ExpenseClaimTestCase):
    def test_stale_instances_register_once(self):
        claim = self._create_claim()
        first = type(claim).objects.get(pk=claim.pk).register_and_bookkeep(self.user)
        second = type(claim).objects.get(pk=claim.pk).register_and_bookkeep(self.user)
        self.assertEqual(first.pk, second.pk)
