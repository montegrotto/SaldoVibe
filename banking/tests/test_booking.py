from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from attachments.models import TransactionAttachment
from banking.models import BankAccount, BankTransaction
from banking.services import ensure_tax_account_for_company, get_quick_booking_suggestion
from banking.tests.base import BankingTestCase
from bookkeeping.models import Account, AccountClass, JournalEntry


class BankBookingTests(BankingTestCase):
    def test_book_transaction_creates_verification(self):
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-20",
            description="Inbetalning",
            amount="1500.00",
            external_id="tx-2",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "counter_account[]": [str(self.counter_account.pk)],
                "counter_amount[]": ["1500.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self._transaction_list_url_for_source())
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertIsNotNone(bank_tx.booked_transaction_id)
        self.assertEqual(bank_tx.booked_transaction.entries.count(), 2)

    def test_selected_attachment_is_linked_when_booking_manually(self):
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-20",
            description="Inbetalning med underlag",
            amount="1500.00",
            external_id="tx-attachment-1",
        )
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "underlag.pdf",
                b"%PDF-1.4 underlag",
                content_type="application/pdf",
            ),
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "counter_account[]": [str(self.counter_account.pk)],
                "counter_amount[]": ["1500.00"],
                "selected_attachment_ids": str(attachment.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertEqual(bank_tx.booked_transaction.attachments.count(), 1)

    def test_book_transaction_form_page_loads(self):
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date="2026-06-20",
            description="Inbetalning",
            amount="1500.00",
            external_id="tx-form-1",
        )

        response = self.client.get(reverse("banking:book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bokför banktransaktion")
        self.assertContains(response, "Mot faktura, utlägg eller lön")
        self.assertContains(response, "Helt manuellt")

    def test_can_book_imported_skattekonto_transaction_from_list(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-02",
            description="Skattekonto inbetalning",
            amount="1000.00",
            external_id="skat-book-1",
        )

        response = self.client.post(
            reverse("banking:book_transaction", args=[bank_tx.pk]),
            {
                "counter_account[]": [str(self.counter_account.pk)],
                "counter_amount[]": ["1000.00"],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('banking:transaction_list')}?bank_account={tax_source.pk}")
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertIsNotNone(bank_tx.booked_transaction_id)

    def test_quick_booking_suggestion_for_intaktsranta(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        income_account = Account.objects.create(
            company=self.company,
            number="8314",
            name="Skattefria ränteintäkter",
            account_class=AccountClass.FINANCIAL,
            is_active=True,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-03",
            description="Intäktsränta januari",
            amount="341.00",
            external_id="skat-interest-ui-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], income_account)
        self.assertEqual(suggestion["rule_label"], "Intäktsränta")

    def test_quick_booking_suggestion_for_korrigerad_intaktsranta(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        income_account = Account.objects.create(
            company=self.company,
            number="8314",
            name="Skattefria ränteintäkter",
            account_class=AccountClass.FINANCIAL,
            is_active=True,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-03",
            description="Korrigerad intäktsränta april",
            amount="-50.00",
            external_id="skat-interest-correction-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], income_account)
        self.assertEqual(suggestion["rule_label"], "Intäktsränta")

    def test_quick_booking_suggestions_for_tax_descriptions(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")

        prelim_account = Account.objects.create(
            company=self.company,
            number="2510",
            name="Skatteskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        ag_account = Account.objects.create(
            company=self.company,
            number="2730",
            name="Avräkning lagstadgade sociala avgifter",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        withheld_tax_account = Account.objects.create(
            company=self.company,
            number="2710",
            name="Personalens källskatt",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        vat_account = Account.objects.create(
            company=self.company,
            number="2650",
            name="Redovisningskonto för moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )

        cases = [
            ("Debiterad preliminärskatt juli", prelim_account, "Debiterad preliminärskatt"),
            ("Arbetsgivaravgift juni", ag_account, "Arbetsgivaravgift"),
            ("Avdragen skatt juni", withheld_tax_account, "Avdragen skatt"),
            ("Moms april", vat_account, "Moms"),
        ]

        for idx, (description, expected_account, expected_rule_label) in enumerate(cases, start=1):
            tx = BankTransaction.objects.create(
                company=self.company,
                bank_account=tax_source,
                date="2026-07-03",
                description=description,
                amount="-1000.00",
                external_id=f"quick-rule-{idx}",
            )

            suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=tx)

            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion["counter_account"], expected_account)
            self.assertEqual(suggestion["rule_label"], expected_rule_label)

    def test_quick_booking_suggestion_for_inbetalning_bokford(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-11",
            description="Inbetalning bokförd 240115",
            amount="100000.00",
            external_id="skat-inbetalning-1",
        )

        suggestion = get_quick_booking_suggestion(company=self.company, bank_tx=bank_tx)

        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion["counter_account"], self.bank_gl_account)
        self.assertEqual(suggestion["rule_label"], "Inbetalning bokförd")

    def test_quick_book_transaction_for_intaktsranta_posts_to_8314(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        income_account = Account.objects.create(
            company=self.company,
            number="8314",
            name="Skattefria ränteintäkter",
            account_class=AccountClass.FINANCIAL,
            is_active=True,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-03",
            description="Intäktsränta januari",
            amount="341.00",
            external_id="skat-interest-quick-1",
        )

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('banking:transaction_list')}?bank_account={tax_source.pk}")
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertIsNotNone(bank_tx.booked_transaction_id)

        entries = JournalEntry.objects.filter(transaction=bank_tx.booked_transaction)
        self.assertEqual(entries.count(), 2)
        self.assertTrue(
            entries.filter(
                account=tax_source.bookkeeping_account, debit=Decimal("341.00"), credit=Decimal("0.00")
            ).exists()
        )
        self.assertTrue(
            entries.filter(account=income_account, debit=Decimal("0.00"), credit=Decimal("341.00")).exists()
        )

    def test_quick_book_transaction_for_debiterad_preliminarskatt_posts_to_2510(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        prelim_account = Account.objects.create(
            company=self.company,
            number="2510",
            name="Skatteskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-10",
            description="Debiterad preliminärskatt juli",
            amount="-3500.00",
            external_id="skat-prelim-quick-1",
        )

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('banking:transaction_list')}?bank_account={tax_source.pk}")
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertIsNotNone(bank_tx.booked_transaction_id)

        entries = JournalEntry.objects.filter(transaction=bank_tx.booked_transaction)
        self.assertEqual(entries.count(), 2)
        self.assertTrue(
            entries.filter(account=prelim_account, debit=Decimal("3500.00"), credit=Decimal("0.00")).exists()
        )
        self.assertTrue(
            entries.filter(
                account=tax_source.bookkeeping_account, debit=Decimal("0.00"), credit=Decimal("3500.00")
            ).exists()
        )

    def test_quick_book_transaction_uses_bank_voucher_series(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")
        Account.objects.create(
            company=self.company,
            number="2510",
            name="Skatteskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        bank_tx = BankTransaction.objects.create(
            company=self.company,
            bank_account=tax_source,
            date="2026-07-12",
            description="Debiterad preliminärskatt juli",
            amount="-3500.00",
            external_id="skat-prelim-series-1",
        )

        response = self.client.post(reverse("banking:quick_book_transaction", args=[bank_tx.pk]))

        self.assertEqual(response.status_code, 302)
        bank_tx.refresh_from_db()
        self.assertTrue(bank_tx.is_booked)
        self.assertIsNotNone(bank_tx.booked_transaction_id)
        self.assertEqual(bank_tx.booked_transaction.voucher_series, "B")
        self.assertIsNotNone(bank_tx.booked_transaction.voucher_number)
        self.assertTrue((bank_tx.booked_transaction.reference or "").startswith("B"))
