from datetime import date
from decimal import Decimal

from django.urls import reverse

from bookkeeping.models import PeriodLock, Transaction
from bookkeeping.payables import register_manual_payment

from .test_expense_claims import ExpenseClaimTestCase


class ExpenseClaimCorrectionTests(ExpenseClaimTestCase):
    def _correct(self, claim):
        return self.client.post(reverse("expenses:expense_correct", args=[claim.pk]), follow=True)

    def test_correction_reverses_voucher_and_returns_claim_to_draft(self):
        claim = self._create_claim()
        original = claim.register_and_bookkeep(self.user)

        response = self._correct(claim)

        self.assertContains(response, "Utläggets verifikation har korrigerats.")
        reversal = Transaction.objects.get(correction_of=original)
        self.assertEqual(reversal.date, date(2026, 7, 5))
        self.assertEqual(reversal.voucher_series, original.voucher_series)
        mirrored = {(e.account_id, e.credit, e.debit) for e in original.entries.all()}
        self.assertEqual({(e.account_id, e.debit, e.credit) for e in reversal.entries.all()}, mirrored)

        claim.refresh_from_db()
        self.assertFalse(claim.is_registered)
        self.assertIsNone(claim.registered_transaction)

        # Rättat utkast bokförs om med en ny verifikation.
        claim.amount_ex_vat, claim.vat_amount, claim.total_amount = Decimal("800"), Decimal("200"), Decimal("1000")
        claim.save()
        rebooked = claim.register_and_bookkeep(self.user)
        self.assertNotEqual(rebooked.pk, original.pk)
        self.assertEqual(rebooked.entries.get(account=self.liability_account).credit, Decimal("1000.00"))

    def test_paid_claim_cannot_be_corrected(self):
        claim = self._create_claim()
        original = claim.register_and_bookkeep(self.user)
        register_manual_payment(
            claim,
            self.user,
            payment_date=date(2026, 7, 10),
            amount=Decimal("100.00"),
            payment_account=self.bank_account,
        )

        detail = self.client.get(reverse("expenses:expense_detail", args=[claim.pk]))
        self.assertNotContains(detail, reverse("expenses:expense_correct", args=[claim.pk]))

        response = self._correct(claim)

        self.assertContains(response, "Utlägget har utbetalningar. Ångra dem först och korrigera sedan utlägget.")
        self.assertFalse(original.corrections.exists())
        claim.refresh_from_db()
        self.assertTrue(claim.is_registered)

    def test_claim_in_locked_period_cannot_be_corrected(self):
        claim = self._create_claim()
        original = claim.register_and_bookkeep(self.user)
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start="2026-07-01",
            period_end="2026-07-31",
            is_locked=True,
        )

        response = self._correct(claim)

        self.assertContains(response, "Perioden är låst – utlägget kan inte korrigeras.")
        self.assertFalse(original.corrections.exists())
