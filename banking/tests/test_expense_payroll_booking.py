from decimal import Decimal

from django.urls import reverse

from banking.models import BankTransaction
from banking.services import get_manual_booking_invoice_options, get_quick_booking_suggestion
from banking.tests.base import BankingTestCase
from bookkeeping.models import Account, AccountClass, JournalEntry, Transaction
from expenses.models import ExpenseClaim, ExpenseClaimPayment
from payroll.models import PayrollRun


class ExpensePayrollBookingTestCase(BankingTestCase):
    def setUp(self):
        super().setUp()
        self.liability_account = Account.objects.create(
            company=self.company,
            number="2820",
            name="Kortfristiga skulder till anställda",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )

    def _create_expense_claim(self, *, total, expense_date="2026-07-05", description="Tangentbord"):
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date=expense_date,
            description=f"Utlägg bokning {description}",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=total,
            credit=Decimal("0.00"),
            description="Kostnad",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.liability_account,
            debit=Decimal("0.00"),
            credit=total,
            description="Skuld utlägg",
        )
        return ExpenseClaim.objects.create(
            company=self.company,
            accounting_year=self.year,
            person_name="Mattias Utläggare",
            description=description,
            expense_date=expense_date,
            expense_account=self.counter_account,
            liability_account=self.liability_account,
            amount_ex_vat=total,
            total_amount=total,
            vat_amount=Decimal("0.00"),
            is_registered=True,
            registered_transaction=booking_tx,
        )

    def _create_payroll_run(self, *, net_salary_total, payment_date="2026-07-25"):
        booking_tx = Transaction.objects.create(
            accounting_year=self.year,
            date=payment_date,
            description="Lönekörning bokning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.counter_account,
            debit=net_salary_total,
            credit=Decimal("0.00"),
            description="Lönekostnad",
        )
        JournalEntry.objects.create(
            transaction=booking_tx,
            account=self.salary_liability_account,
            debit=Decimal("0.00"),
            credit=net_salary_total,
            description="Nettolön att utbetala",
        )
        return PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date=payment_date,
            is_finished=True,
            booking_transaction=booking_tx,
        )

    def _create_bank_tx(self, *, amount, date="2026-07-25", external_id="bank-ep-x"):
        return BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date=date,
            description="Utbetalning",
            amount=amount,
            external_id=external_id,
        )


class ExpenseClaimBookingTests(ExpensePayrollBookingTestCase):
    def test_options_include_open_expense_claims(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"))
        bank_tx = self._create_bank_tx(amount="-1200.00", external_id="bank-exp-opt")

        options = get_manual_booking_invoice_options(company=self.company, bank_tx=bank_tx)
        option_ids = {item["id"] for item in options["expense_claim"]}
        self.assertIn(str(claim.pk), option_ids)

    def test_book_bank_transaction_against_expense_claim(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"))
        bank_tx = self._create_bank_tx(amount="-1200.00", external_id="bank-exp-full")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "expense_claim",
                "expense_alloc_invoice[]": [str(claim.pk)],
                "expense_alloc_amount[]": ["1200.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertTrue(claim.is_paid)
        self.assertEqual(claim.paid_amount, Decimal("1200.00"))
        self.assertEqual(claim.payment_transaction_id, bank_tx.booked_transaction_id)

        liability_entry = JournalEntry.objects.get(
            transaction=bank_tx.booked_transaction, account=self.liability_account
        )
        self.assertEqual(liability_entry.debit, Decimal("1200.00"))
        payment = ExpenseClaimPayment.objects.get(payable=claim)
        self.assertEqual(payment.amount, Decimal("1200.00"))

    def test_partial_expense_claim_payment(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"))
        bank_tx = self._create_bank_tx(amount="-500.00", external_id="bank-exp-part")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "expense_claim",
                "expense_alloc_invoice[]": [str(claim.pk)],
                "expense_alloc_amount[]": ["500.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        self.assertFalse(claim.is_paid)
        self.assertTrue(claim.is_partially_paid)
        self.assertEqual(claim.remaining_amount, Decimal("700.00"))

    def test_expense_claim_rejected_for_incoming_payment(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"))
        bank_tx = self._create_bank_tx(amount="1200.00", external_id="bank-exp-wrongdir")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "expense_claim",
                "expense_alloc_invoice[]": [str(claim.pk)],
                "expense_alloc_amount[]": ["1200.00"],
            },
        )

        self.assertEqual(response.status_code, 200)
        claim.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        self.assertEqual(claim.paid_amount, Decimal("0.00"))

    def test_quick_booking_suggests_expense_claim(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"), expense_date="2026-07-20")
        bank_tx = self._create_bank_tx(amount="-1200.00", date="2026-07-25", external_id="bank-exp-sugg")

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["source_type"], "expense_claim")
        self.assertEqual(suggestion["source_id"], claim.pk)

    def test_quick_book_settles_expense_claim(self):
        claim = self._create_expense_claim(total=Decimal("1200.00"), expense_date="2026-07-20")
        bank_tx = self._create_bank_tx(amount="-1200.00", date="2026-07-25", external_id="bank-exp-quick")

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertTrue(claim.is_paid)


class MixedExpensePayrollTests(ExpensePayrollBookingTestCase):
    def test_match_mode_mixes_payroll_run_and_expense_claim(self):
        run = self._create_payroll_run(net_salary_total=Decimal("30000.00"))
        claim = self._create_expense_claim(total=Decimal("1200.00"))
        bank_tx = self._create_bank_tx(amount="-31200.00", external_id="bank-mixed-pay-exp")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "match",
                "match_alloc_invoice[]": [
                    f"payroll_run:{run.pk}",
                    f"expense_claim:{claim.pk}",
                ],
                "match_alloc_amount[]": ["30000.00", "1200.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        claim.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertEqual(run.paid_amount, Decimal("30000.00"))
        self.assertTrue(claim.is_paid)

        salary_entry = JournalEntry.objects.get(
            transaction=bank_tx.booked_transaction, account=self.salary_liability_account
        )
        self.assertEqual(salary_entry.debit, Decimal("30000.00"))
        liability_entry = JournalEntry.objects.get(
            transaction=bank_tx.booked_transaction, account=self.liability_account
        )
        self.assertEqual(liability_entry.debit, Decimal("1200.00"))


class PayrollRunBookingTests(ExpensePayrollBookingTestCase):
    def test_options_include_unpaid_payroll_runs(self):
        run = self._create_payroll_run(net_salary_total=Decimal("54000.00"))
        bank_tx = self._create_bank_tx(amount="-27000.00", external_id="bank-pay-opt")

        options = get_manual_booking_invoice_options(company=self.company, bank_tx=bank_tx)
        option_ids = {item["id"] for item in options["payroll_run"]}
        self.assertIn(str(run.pk), option_ids)

    def test_partial_salary_transfers_settle_payroll_run(self):
        run = self._create_payroll_run(net_salary_total=Decimal("54000.00"))

        first_tx = self._create_bank_tx(amount="-30000.00", external_id="bank-pay-1")
        response = self.client.post(
            reverse("banking:book_transaction", args=[first_tx.pk]),
            {
                "booking_mode": "payroll_run",
                "payroll_alloc_invoice[]": [str(run.pk)],
                "payroll_alloc_amount[]": ["30000.00"],
            },
        )
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.paid_amount, Decimal("30000.00"))
        self.assertEqual(run.salary_payment_remaining(), Decimal("24000.00"))

        second_tx = self._create_bank_tx(amount="-24000.00", external_id="bank-pay-2")
        response = self.client.post(
            reverse("banking:book_transaction", args=[second_tx.pk]),
            {
                "booking_mode": "payroll_run",
                "payroll_alloc_invoice[]": [str(run.pk)],
                "payroll_alloc_amount[]": ["24000.00"],
            },
        )
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.paid_amount, Decimal("54000.00"))
        self.assertEqual(run.salary_payment_remaining(), Decimal("0.00"))

        second_tx.refresh_from_db()
        salary_entry = JournalEntry.objects.get(
            transaction=second_tx.booked_transaction, account=self.salary_liability_account
        )
        self.assertEqual(salary_entry.debit, Decimal("24000.00"))

        # Fully paid runs disappear from the options.
        third_tx = self._create_bank_tx(amount="-1000.00", external_id="bank-pay-3")
        options = get_manual_booking_invoice_options(company=self.company, bank_tx=third_tx)
        self.assertEqual(options["payroll_run"], [])

    def test_overallocation_against_payroll_run_rejected(self):
        run = self._create_payroll_run(net_salary_total=Decimal("54000.00"))
        bank_tx = self._create_bank_tx(amount="-60000.00", external_id="bank-pay-over")

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "booking_mode": "payroll_run",
                "payroll_alloc_invoice[]": [str(run.pk)],
                "payroll_alloc_amount[]": ["60000.00"],
            },
        )

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertFalse(bank_tx.is_booked)
        self.assertEqual(run.paid_amount, Decimal("0.00"))

    def test_quick_book_does_not_suggest_unrelated_partial_amount(self):
        # An amount that merely fits under the remaining balance must not be auto-suggested
        # as a partial payroll payment - only an exact match may be quick-booked automatically.
        self._create_payroll_run(net_salary_total=Decimal("54000.00"))
        bank_tx = self._create_bank_tx(amount="-953.00", external_id="bank-pay-quick")

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)
        self.assertIsNone(suggestion)

    def test_quick_book_settles_payroll_run_on_exact_match(self):
        run = self._create_payroll_run(net_salary_total=Decimal("54000.00"))
        bank_tx = self._create_bank_tx(amount="-54000.00", external_id="bank-pay-quick")

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["source_type"], "payroll_run")

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertEqual(run.paid_amount, Decimal("54000.00"))
