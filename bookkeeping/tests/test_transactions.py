from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse

from attachments.models import TransactionAttachment
from bookkeeping.models import Account, AccountClass, AccountingYear, JournalEntry, Transaction, VerificationTemplate
from saldovibe.testing import CompanyTestCase, create_account


class TransactionPostTests(CompanyTestCase):
    user_email = "txn-user@example.com"
    company_name = "Transaktionsbolag AB"
    company_org_number = "556677-1122"

    def setUp(self):
        super().setUp()
        self.debit_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.credit_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)

    def test_transaction_post_succeeds_without_accounting_year_in_payload(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-26",
                "description": "Manuell verifikation",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": str(self.debit_account.pk),
                "entries-0-debit": "100.00",
                "entries-0-credit": "0.00",
                "entries-1-account": str(self.credit_account.pk),
                "entries-1-debit": "0.00",
                "entries-1-credit": "100.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:transaction_list"))
        txn = Transaction.objects.get(description="Manuell verifikation")
        self.assertEqual(txn.accounting_year_id, self.year.pk)
        self.assertEqual(txn.voucher_series, "A")
        self.assertEqual(txn.voucher_number, 1)
        self.assertEqual(txn.reference, "A1")

    def test_selected_attachment_is_linked_on_post(self):

        from attachments.models import TransactionAttachment

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
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-26",
                "description": "Manuell verifikation med bilaga",
                "selected_attachment_ids": str(attachment.pk),
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": str(self.debit_account.pk),
                "entries-0-debit": "100.00",
                "entries-0-credit": "0.00",
                "entries-1-account": str(self.credit_account.pk),
                "entries-1-debit": "0.00",
                "entries-1-credit": "100.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:transaction_list"))
        txn = Transaction.objects.get(description="Manuell verifikation med bilaga")
        self.assertEqual(txn.attachments.count(), 1)

    def test_attachment_can_be_added_to_a_posted_transaction_in_an_open_period(self):
        from attachments.models import TransactionAttachment

        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Bokförd verifikation",
            created_by=self.user,
        )
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "eftersand.pdf",
                b"%PDF-1.4 eftersand",
                content_type="application/pdf",
            ),
        )

        response = self.client.post(
            reverse("bookkeeping:transaction_attachment_add", args=[txn.pk]),
            {"selected_attachment_ids": str(attachment.pk)},
        )

        self.assertRedirects(response, reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertEqual(txn.attachments.count(), 1)

    def test_attachment_cannot_be_added_or_removed_when_period_is_locked(self):
        from attachments.models import TransactionAttachment
        from bookkeeping.models import PeriodLock

        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-01-15",
            description="Bokförd verifikation i låst period",
            created_by=self.user,
        )
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "kvitto.pdf",
                b"%PDF-1.4 kvitto",
                content_type="application/pdf",
            ),
        )
        txn.attachments.add(attachment)
        other_attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "nytt-kvitto.pdf",
                b"%PDF-1.4 nytt",
                content_type="application/pdf",
            ),
        )
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start="2026-01-01",
            period_end="2026-01-31",
            is_locked=True,
            reason="Stängd period",
            locked_by=self.user,
        )

        add_response = self.client.post(
            reverse("bookkeeping:transaction_attachment_add", args=[txn.pk]),
            {"selected_attachment_ids": str(other_attachment.pk)},
        )
        self.assertRedirects(add_response, reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertEqual(txn.attachments.count(), 1)

        remove_response = self.client.post(
            reverse("bookkeeping:transaction_attachment_remove", args=[txn.pk]),
            {"attachment_id": str(attachment.pk)},
        )
        self.assertRedirects(remove_response, reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertEqual(txn.attachments.count(), 1)
        attachment.refresh_from_db()
        self.assertIsNone(attachment.deleted_at)

    def test_removing_an_attachment_only_unlinks_it_from_the_transaction(self):
        from attachments.models import TransactionAttachment

        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Bokförd verifikation",
            created_by=self.user,
        )
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
        )
        txn.attachments.add(attachment)

        response = self.client.post(
            reverse("bookkeeping:transaction_attachment_remove", args=[txn.pk]),
            {"attachment_id": str(attachment.pk)},
        )

        self.assertRedirects(response, reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertEqual(txn.attachments.count(), 0)
        attachment.refresh_from_db()
        self.assertIsNone(attachment.deleted_at)

    def test_voucher_counter_resets_per_accounting_year_for_series_a(self):
        next_year = AccountingYear.objects.create(
            company=self.company,
            start_date="2027-01-01",
            end_date="2027-12-31",
        )

        txn_2026 = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Första verifikation 2026",
            created_by=self.user,
        )
        txn_2027 = Transaction.objects.create(
            accounting_year=next_year,
            date="2027-01-15",
            description="Första verifikation 2027",
            created_by=self.user,
        )

        self.assertEqual(txn_2026.voucher_series, "A")
        self.assertEqual(txn_2026.voucher_number, 1)
        self.assertEqual(txn_2026.reference, "A1")

        self.assertEqual(txn_2027.voucher_series, "A")
        self.assertEqual(txn_2027.voucher_number, 1)
        self.assertEqual(txn_2027.reference, "A1")

    def test_transaction_reverse_creates_correction_transaction(self):
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Original",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn,
            account=self.debit_account,
            debit="100.00",
            credit="0.00",
        )
        JournalEntry.objects.create(
            transaction=txn,
            account=self.credit_account,
            debit="0.00",
            credit="100.00",
        )

        response = self.client.post(reverse("bookkeeping:transaction_reverse", args=[txn.pk]))

        self.assertEqual(response.status_code, 302)
        reversal = Transaction.objects.get(correction_of=txn)
        self.assertEqual(reversal.entries.count(), 2)
        self.assertTrue(reversal.is_balanced)

        reversal_debit_entry = reversal.entries.get(account=self.debit_account)
        self.assertEqual(reversal_debit_entry.debit, Decimal("0.00"))
        self.assertEqual(reversal_debit_entry.credit, Decimal("100.00"))

    def test_transaction_list_shows_corrected_transaction_and_reversal(self):
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Original",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=self.debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=self.credit_account, debit="0.00", credit="100.00")

        reversal_response = self.client.post(reverse("bookkeeping:transaction_reverse", args=[txn.pk]))
        self.assertEqual(reversal_response.status_code, 302)

        response = self.client.get(reverse("bookkeeping:transaction_list"), {"year": self.year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Original")
        self.assertContains(response, "Korrigering av verifikation")
        self.assertEqual(len(response.context["transactions"]), 2)

    def test_transaction_detail_shows_corrected_transaction_and_reversal_with_cross_links(self):
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Original",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=self.debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=self.credit_account, debit="0.00", credit="100.00")

        self.client.post(reverse("bookkeeping:transaction_reverse", args=[txn.pk]))
        reversal = Transaction.objects.get(correction_of=txn)

        original_response = self.client.get(reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertEqual(original_response.status_code, 200)
        self.assertContains(original_response, "Korrigerad av")
        self.assertContains(original_response, reverse("bookkeeping:transaction_detail", args=[reversal.pk]))
        self.assertNotContains(original_response, "Skapa korrigering")

        reversal_response = self.client.get(reverse("bookkeeping:transaction_detail", args=[reversal.pk]))
        self.assertEqual(reversal_response.status_code, 200)
        self.assertContains(reversal_response, "Korrigering av")
        self.assertContains(reversal_response, reverse("bookkeeping:transaction_detail", args=[txn.pk]))
        self.assertNotContains(reversal_response, "Skapa korrigering")

    def test_balance_sheet_account_rows_link_to_filtered_transaction_list(self):
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Balansrad",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=self.debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=self.credit_account, debit="0.00", credit="100.00")

        response = self.client.get(reverse("bookkeeping:balance_sheet"), {"year": self.year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{reverse('bookkeeping:transaction_list')}?year={self.year.pk}&amp;account={self.debit_account.pk}",
        )

    def test_income_statement_account_rows_link_to_filtered_transaction_list(self):
        revenue_account = Account.objects.create(
            company=self.company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            is_active=True,
        )
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Intäkt",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=revenue_account, debit="0.00", credit="250.00")

        response = self.client.get(reverse("bookkeeping:income_statement"), {"year": self.year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{reverse('bookkeeping:transaction_list')}?year={self.year.pk}&amp;account={revenue_account.pk}",
        )

    def test_transaction_list_filters_by_account_and_shows_selected_account(self):
        other_account = Account.objects.create(
            company=self.company,
            number="1931",
            name="Andra bankkonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )
        matching_txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Matchar konto",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=matching_txn, account=self.debit_account, debit="100.00", credit="0.00")
        JournalEntry.objects.create(
            transaction=matching_txn, account=self.credit_account, debit="0.00", credit="100.00"
        )
        other_txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-27",
            description="Annat konto",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=other_txn, account=other_account, debit="200.00", credit="0.00")
        JournalEntry.objects.create(transaction=other_txn, account=self.credit_account, debit="0.00", credit="200.00")

        response = self.client.get(
            reverse("bookkeeping:transaction_list"),
            {"year": self.year.pk, "account": self.debit_account.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_account"].pk, self.debit_account.pk)
        self.assertEqual(len(response.context["transactions"]), 1)
        self.assertContains(response, "Visar konto:")
        self.assertContains(response, self.debit_account.name)

    def test_transaction_post_errors_when_date_does_not_match_any_accounting_year(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2027-01-02",
                "description": "Utanför år",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": str(self.debit_account.pk),
                "entries-0-debit": "100.00",
                "entries-0-credit": "0.00",
                "entries-1-account": str(self.credit_account.pk),
                "entries-1-debit": "0.00",
                "entries-1-credit": "100.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inget räkenskapsår matchar valt datum.")
        self.assertContains(response, "Utanför år")
        self.assertFalse(Transaction.objects.filter(description="Utanför år").exists())

    def test_transaction_post_rejects_row_with_both_debit_and_credit(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-26",
                "description": "Ogiltig dubbelrad",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": str(self.debit_account.pk),
                "entries-0-debit": "100.00",
                "entries-0-credit": "100.00",
                "entries-1-account": str(self.credit_account.pk),
                "entries-1-debit": "0.00",
                "entries-1-credit": "100.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "En konteringsrad kan inte ha belopp i både debet och kredit.")
        self.assertFalse(Transaction.objects.filter(description="Ogiltig dubbelrad").exists())

    def test_transaction_post_rejects_unbalanced_entries(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-26",
                "description": "Obalanserad verifikation",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": str(self.debit_account.pk),
                "entries-0-debit": "100.00",
                "entries-0-credit": "0.00",
                "entries-1-account": str(self.credit_account.pk),
                "entries-1-debit": "0.00",
                "entries-1-credit": "80.00",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verifikationen är inte i balans")
        self.assertFalse(Transaction.objects.filter(description="Obalanserad verifikation").exists())

    def test_journal_entry_constraint_rejects_both_debit_and_credit(self):
        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-26",
            description="Constraint test",
            created_by=self.user,
        )
        from bookkeeping.models import JournalEntry

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.create(
                    transaction=txn,
                    account=self.debit_account,
                    debit="100.00",
                    credit="100.00",
                )


class TransactionAddExtractionSuggestionTests(CompanyTestCase):
    user_email = "txn-extraction-user@example.com"
    company_name = "Verifikationsextraktion AB"
    company_org_number = "556677-2233"

    def setUp(self):
        super().setUp()
        self.debit_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.credit_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)

    def _attachment_with(self, extracted_data):
        return TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", b"%PDF-1.4 kvitto", content_type="application/pdf"),
            extracted_data=extracted_data,
        )

    def test_prefills_date_and_description_from_attachment(self):
        attachment = self._attachment_with(
            {"leverantör": "Kontorsmaterial AB", "datum": "2026-05-04", "totalbelopp": "349.00"}
        )

        response = self.client.get(
            reverse("bookkeeping:transaction_add"),
            {"selected_attachments": str(attachment.pk)},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["date"], "2026-05-04")
        self.assertEqual(form.initial["description"], "Kontorsmaterial AB")
        self.assertTrue(response.context["extraction_applied"])
        self.assertEqual(response.context["extraction_suggested_base_amount"], "349.00")
        self.assertContains(response, "ReInvGrabber")

    def test_template_description_is_not_overridden_by_extraction(self):
        template = VerificationTemplate.objects.create(
            company=self.company,
            name="Kontorsmaterial-mall",
            description="Inköp kontorsmaterial",
        )
        attachment = self._attachment_with({"leverantör": "Kontorsmaterial AB", "datum": "2026-05-04"})

        response = self.client.get(
            reverse("bookkeeping:transaction_add"),
            {"selected_attachments": str(attachment.pk), "template": str(template.pk)},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.fields["description"].initial, "Inköp kontorsmaterial")
        self.assertEqual(form.initial["date"], "2026-05-04")

    def test_no_extraction_data_leaves_form_untouched(self):
        response = self.client.get(reverse("bookkeeping:transaction_add"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["extraction_applied"])
        self.assertEqual(response.context["extraction_suggested_base_amount"], "")
        self.assertNotIn("date", response.context["form"].initial)


class AccountUpdateTests(CompanyTestCase):
    user_email = "account-editor@example.com"
    company_name = "Kontobolag AB"
    company_org_number = "101010-1010"
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.account = create_account(self.company, "3041", "Forsaljning tjanster 25%", AccountClass.REVENUE)

    def test_account_update_saves_vat_field_code(self):
        response = self.client.post(
            reverse("bookkeeping:account_update", args=[self.account.pk]),
            {
                "number": "3041",
                "name": "Forsaljning tjanster 25%",
                "account_class": AccountClass.REVENUE,
                "vat_field_code": "05",
                "description": "",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:account_list"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.vat_field_code, "05")

    def test_account_update_cannot_change_account_number(self):
        response = self.client.post(
            reverse("bookkeeping:account_update", args=[self.account.pk]),
            {
                "number": "9999",
                "name": "Forsaljning tjanster 25%",
                "account_class": AccountClass.REVENUE,
                "vat_field_code": "05",
                "sru_code": "",
                "description": "",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:account_list"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.number, "3041")
