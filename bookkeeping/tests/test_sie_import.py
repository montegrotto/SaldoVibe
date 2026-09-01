import io
import json
import zipfile
from datetime import date
from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from auditlog.models import AuditLogEntry
from bookkeeping.models import Account, AccountClass, JournalEntry, PeriodLock, Transaction, TransactionSource
from saldovibe.testing import (
    CompanyTestCase,
    create_account,
    create_accounting_year,
    create_accounts,
    create_user,
    set_active_company,
)


class SIEImportTests(CompanyTestCase):
    user_email = "sie-user@example.com"
    user_fields = {"is_staff": True}
    company_name = "SIE Bolag AB"
    company_org_number = "556677-0001"
    # Two years: imports are addressed to a specific one, and the balance checks
    # need a preceding year to carry forward from.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.year_2025 = create_accounting_year(self.company, "2025-01-01", "2025-12-31")
        self.year_2026 = create_accounting_year(self.company, "2026-01-01", "2026-12-31")

        create_accounts(
            self.company,
            [
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
            ],
        )

    def test_sie_import_link_is_not_visible_in_global_navigation(self):
        response = self.client.get(reverse("bookkeeping:transaction_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("bookkeeping:sie_import"))

    def test_sie_import_link_is_visible_only_on_accounting_year_edit_page(self):
        list_response = self.client.get(reverse("bookkeeping:accounting_year_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}")

    def test_accounting_year_list_shows_verification_count_per_year(self):
        Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="V1",
            reference="A101",
            created_by=self.user,
        )
        Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-02",
            description="V2",
            reference="A102",
            created_by=self.user,
        )
        Transaction.objects.create(
            accounting_year=self.year_2025,
            date="2025-02-01",
            description="V3",
            reference="B101",
            created_by=self.user,
        )

        response = self.client.get(reverse("bookkeeping:accounting_year_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<td class="text-center">2</td>', html=True)
        self.assertContains(response, '<td class="text-center">1</td>', html=True)

    def test_sie_import_prefills_selected_accounting_year_from_query(self):
        response = self.client.get(f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_year"].pk, self.year_2026.pk)

    def test_sie_import_replaces_existing_year_transactions_when_confirmed(self):
        old_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="Gammal verifikation",
            reference="A999",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )
        preserved = Transaction.objects.create(
            accounting_year=self.year_2025,
            date="2025-02-01",
            description="Ska behållas",
            reference="B001",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
                "confirm_replace": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Transaction.objects.filter(accounting_year=self.year_2026).count(), 1)
        self.assertFalse(Transaction.objects.filter(pk=old_txn.pk).exists())
        self.assertTrue(Transaction.objects.filter(accounting_year=self.year_2026, description="Ny import").exists())
        self.assertTrue(Transaction.objects.filter(pk=preserved.pk, accounting_year=self.year_2025).exists())

    def test_sie_import_preserves_locked_period_transactions_when_replacing(self):
        locked_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-01-15",
            description="Låst verifikation",
            reference="A500",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year_2026,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            reason="Månadsstängning",
        )
        open_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="Öppen verifikation",
            reference="A999",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
                "confirm_replace": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(pk=locked_txn.pk).exists())
        self.assertFalse(Transaction.objects.filter(pk=open_txn.pk).exists())
        self.assertTrue(Transaction.objects.filter(accounting_year=self.year_2026, description="Ny import").exists())

    def test_sie_import_never_deletes_natively_booked_transactions(self):
        native_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="Manuellt bokförd",
            reference="A1",
            created_by=self.user,
        )
        imported_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-02",
            description="Tidigare importerad",
            reference="A999",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
                "confirm_replace": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(pk=native_txn.pk).exists())
        self.assertFalse(Transaction.objects.filter(pk=imported_txn.pk).exists())
        self.assertTrue(Transaction.objects.filter(accounting_year=self.year_2026, description="Ny import").exists())

    def test_sie_import_keeps_both_series_with_the_same_number_and_date(self):
        # The parser used to hardcode series "A", so B1 collided with A1 and was
        # silently dropped as a duplicate.
        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Serie A"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
                '#VER "B" "1" 20260210 "Serie B"',
                "{",
                "#TRANS 1930 77.00",
                "#TRANS 2440 -77.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        references = set(Transaction.objects.filter(accounting_year=self.year_2026).values_list("reference", flat=True))
        self.assertEqual(references, {"A1", "B1"})

    def test_sie_amount_parsing_rejects_oversized_and_sub_ore_amounts(self):
        from bookkeeping.sie import parse_sie_verifications_with_diagnostics

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Trasiga belopp"',
                "{",
                "#TRANS 1930 99999999999999999999.00",
                "#TRANS 2440 10.115",
                "}",
            ]
        )
        verifications, diagnostics = parse_sie_verifications_with_diagnostics(sie_content)

        self.assertEqual(verifications[0]["entries"], [])
        self.assertEqual(len([d for d in diagnostics if "Ogiltigt belopp" in d]), 2)

    def test_sie_date_with_invalid_month_gets_a_swedish_diagnostic(self):
        from bookkeeping.sie import parse_sie_verifications_with_diagnostics

        sie_content = '#VER "A" "1" 20261301 "Fel månad"\n{\n#TRANS 1930 100.00\n#TRANS 2440 -100.00\n}\n'
        verifications, diagnostics = parse_sie_verifications_with_diagnostics(sie_content)

        self.assertEqual(verifications, [])
        self.assertIn("Ogiltigt datum i SIE: 20261301", diagnostics[0])

    def test_sie_import_rejects_file_whose_rar_0_differs_from_selected_year(self):
        sie_content = "\n".join(
            [
                "#RAR -1 20240101 20241231",
                "#RAR 0 20250101 20251231",
                '#VER "A" "1" 20260210 "Fel år"',
                "{",
                "#TRANS 1930 -100.00",
                "#TRANS 2440 100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("wrong-year.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 200)
        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("2025-01-01 – 2025-12-31" in t and "2026-01-01 – 2026-12-31" in t for t in message_texts))
        self.assertFalse(Transaction.objects.filter(accounting_year=self.year_2026).exists())

    def test_sie_import_accepts_file_whose_rar_0_matches_selected_year(self):
        sie_content = "\n".join(
            [
                "#RAR -1 20250101 20251231",
                "#RAR 0 20260101 20261231",
                '#VER "A" "1" 20260210 "Rätt år"',
                "{",
                "#TRANS 1930 -100.00",
                "#TRANS 2440 100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("right-year.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Transaction.objects.filter(accounting_year=self.year_2026).count(), 1)

    def test_sie_import_uses_konto_names_for_new_accounts(self):
        sie_content = "\n".join(
            [
                '#KONTO 6110 "Kontorsmateriel"',
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 -100.00",
                "#TRANS 6110 100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        new_account = Account.objects.get(company=self.company, number="6110")
        self.assertEqual(new_account.name, "Kontorsmateriel")

    def test_sie_import_warns_when_ub_does_not_match_computed_balance(self):
        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
                "#UB 0 1930 999999.00",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Kontrollsumma" in text and "999999.00" in text for text in message_texts), message_texts)

    def test_sie_import_does_not_warn_when_ub_matches_computed_balance(self):
        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
                "#UB 0 1930 100.00",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertFalse(any("Kontrollsumma" in text for text in message_texts), message_texts)

    def test_sie_import_requires_confirmation_when_imported_transactions_exist(self):
        old_txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="Gammal verifikation",
            reference="A998",
            created_by=self.user,
            source=TransactionSource.SIE_IMPORT,
        )

        sie_content = "\n".join(
            [
                '#VER "A" "3" 20260212 "Import kräver bekräftelse"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("needs-confirm.se", sie_content.encode("utf-8"), content_type="text/plain")

        post_response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
            },
        )

        self.assertEqual(post_response.status_code, 200)
        message_texts = [str(m) for m in get_messages(post_response.wsgi_request)]
        self.assertTrue(any("bekräfta" in text for text in message_texts), message_texts)
        self.assertEqual(Transaction.objects.filter(accounting_year=self.year_2026).count(), 1)
        self.assertTrue(Transaction.objects.filter(pk=old_txn.pk).exists())

    def test_sie_import_marks_created_entities_in_auditlog(self):
        sie_content = "\n".join(
            [
                '#VER "A" "2" 20260211 "Import med nytt konto"',
                "{",
                "#TRANS 2999 -100.00",
                "#TRANS 1930 100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import-source.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        txn = Transaction.objects.get(accounting_year=self.year_2026, reference="A2")
        txn_entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.transaction",
            object_pk=str(txn.pk),
            action=AuditLogEntry.Action.CREATE,
        ).latest("id")

        self.assertEqual((txn_entry.metadata or {}).get("source"), "SIE-import")
        self.assertEqual(txn_entry.changes["_audit_source"]["after"], "SIE-import")
        self.assertIn("via SIE-import", txn_entry.summary)

        imported_account = Account.objects.get(company=self.company, number="2999")
        account_entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.account",
            object_pk=str(imported_account.pk),
            action=AuditLogEntry.Action.CREATE,
        ).latest("id")
        self.assertEqual((account_entry.metadata or {}).get("source"), "SIE-import")

    def test_sie_import_skips_locked_periods(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year_2026,
            period_start=date(2026, 2, 10),
            period_end=date(2026, 2, 10),
            reason="Månadsstängning",
        )

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Låst period"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("locked-period.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {
                "sie_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(accounting_year=self.year_2026, reference="A1").exists())

    def test_sie_import_form_is_hidden_when_accounting_year_is_fully_locked(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year_2026,
            period_start=self.year_2026.start_date,
            period_end=self.year_2026.end_date,
            reason="Bokslut",
        )

        response = self.client.get(f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["selected_year_locked"])
        self.assertNotContains(response, 'id="sie-import-submit"')
        self.assertContains(response, "Räkenskapsåret är låst")

    def test_sie_import_post_is_blocked_when_accounting_year_is_fully_locked(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year_2026,
            period_start=self.year_2026.start_date,
            period_end=self.year_2026.end_date,
            reason="Bokslut",
        )

        sie_content = "\n".join(
            [
                '#VER "A" "1" 20260210 "Ska blockeras"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("blocked.se", sie_content.encode("utf-8"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(accounting_year=self.year_2026, reference="A1").exists())

    def test_sie_import_link_is_disabled_on_accounting_year_list_when_locked(self):
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year_2026,
            period_start=self.year_2026.start_date,
            period_end=self.year_2026.end_date,
            reason="Bokslut",
        )

        response = self.client.get(reverse("bookkeeping:accounting_year_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="btn btn-outline-primary btn-sm disabled"')
        self.assertNotContains(response, f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}")

    def test_si_import_link_is_visible_on_transaction_list(self):
        response = self.client.get(reverse("bookkeeping:transaction_list"), {"year": self.year_2026.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{reverse('bookkeeping:si_import')}?year={self.year_2026.pk}")

    def test_si_import_adds_verifications_without_removing_existing(self):
        existing = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-01",
            description="Befintlig verifikation",
            reference="A10",
            created_by=self.user,
        )

        sie_content = "\n".join(
            [
                "#SIETYP 4",
                '#VER "A" "9" 20260213 "SI fil"',
                "{",
                "#TRANS 1930 200.00",
                "#TRANS 2440 -200.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.SI", sie_content.encode("cp437"), content_type="text/plain")

        upload_response = self.client.post(
            f"{reverse('bookkeeping:si_import')}?year={self.year_2026.pk}",
            {
                "action": "upload",
                "si_file": upload,
            },
        )

        self.assertEqual(upload_response.status_code, 200)
        self.assertContains(upload_response, "Steg 2 av 2")
        self.assertContains(upload_response, "SI fil")

        response = self.client.post(
            f"{reverse('bookkeeping:si_import')}?year={self.year_2026.pk}",
            {
                "action": "confirm",
                "account_map_0_0": "1930",
                "account_map_0_1": "2440",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Transaction.objects.filter(pk=existing.pk).exists())
        imported_txn = Transaction.objects.get(accounting_year=self.year_2026, description="SI fil")
        self.assertTrue(imported_txn.reference.startswith("SI-Import"))

    def test_si_import_preview_warns_for_likely_duplicates(self):
        existing = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-13",
            description="SI fil",
            reference="MAN1",
            created_by=self.user,
        )
        account_1930 = Account.objects.get(company=self.company, number="1930")
        account_2440 = Account.objects.get(company=self.company, number="2440")
        JournalEntry.objects.create(
            transaction=existing, account=account_1930, debit=Decimal("200.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=existing, account=account_2440, debit=Decimal("0.00"), credit=Decimal("200.00")
        )

        si_content = "\n".join(
            [
                "#SIETYP 4",
                '#VER "A" "9" 20260213 "SI fil"',
                "{",
                "#TRANS 1930 200.00",
                "#TRANS 2440 -200.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import-duplicate.SI", si_content.encode("cp437"), content_type="text/plain")

        response = self.client.post(
            f"{reverse('bookkeeping:si_import')}?year={self.year_2026.pk}",
            {
                "action": "upload",
                "si_file": upload,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dublettvarning")
        self.assertContains(response, "Möjlig dublett")
        self.assertContains(response, "MAN1")

    def test_sie4_export_downloads_selected_year(self):
        txn_2026 = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-13",
            description="Exporttest 2026",
            created_by=self.user,
        )
        account_1930 = Account.objects.get(company=self.company, number="1930")
        account_2440 = Account.objects.get(company=self.company, number="2440")
        JournalEntry.objects.create(
            transaction=txn_2026, account=account_1930, debit=Decimal("200.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn_2026, account=account_2440, debit=Decimal("0.00"), credit=Decimal("200.00")
        )

        txn_2025 = Transaction.objects.create(
            accounting_year=self.year_2025,
            date="2025-02-13",
            description="Exporttest 2025",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn_2025, account=account_1930, debit=Decimal("100.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn_2025, account=account_2440, debit=Decimal("0.00"), credit=Decimal("100.00")
        )

        response = self.client.get(f"{reverse('bookkeeping:sie_export')}?year={self.year_2026.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        content = response.content.decode("cp437")
        self.assertIn("#SIETYP 4", content)
        self.assertIn("Exporttest 2026", content)
        self.assertNotIn("Exporttest 2025", content)

    def test_sie4_export_includes_ib_ub_res_sru(self):
        account_1930 = Account.objects.get(company=self.company, number="1930")
        account_2440 = Account.objects.get(company=self.company, number="2440")
        revenue_account = Account.objects.create(
            company=self.company,
            number="3001",
            name="Försäljning",
            account_class=AccountClass.REVENUE,
            is_active=True,
        )

        txn_2025 = Transaction.objects.create(
            accounting_year=self.year_2025,
            date="2025-02-13",
            description="Ingaende saldo 2025",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn_2025, account=account_1930, debit=Decimal("100.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn_2025, account=account_2440, debit=Decimal("0.00"), credit=Decimal("100.00")
        )

        txn_2026 = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-13",
            description="Forsaljning 2026",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn_2026, account=account_1930, debit=Decimal("200.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn_2026, account=revenue_account, debit=Decimal("0.00"), credit=Decimal("200.00")
        )

        response = self.client.get(f"{reverse('bookkeeping:sie_export')}?year={self.year_2026.pk}")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("cp437")

        # Account 1930 (BAS 1900-1999, "Kassa, bank och redovisningsmedel") gets an
        # SRU code assigned automatically on creation (see bookkeeping/sru_lookup.py).
        self.assertIn("#SRU 1930 7281", content)
        # Opening balance carried from the 2025 postings, closing balance includes 2026's too.
        self.assertIn("#IB 0 1930 100.00", content)
        self.assertIn("#UB 0 1930 300.00", content)
        # Only this year's revenue posting counts towards #RES, not the prior year's.
        self.assertIn("#RES 0 3001 -200.00", content)

    def test_sie_and_si_import_are_blocked_for_non_finance_admin(self):
        non_staff = create_user("sie-import-nonstaff@example.com", is_staff=False)
        self.company.users.add(non_staff)
        set_active_company(self.client, self.company)
        self.client.force_login(non_staff)

        for url_name in ("bookkeeping:sie_import", "bookkeeping:si_import"):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

    def test_sie_export_is_blocked_for_non_finance_admin(self):
        non_staff = create_user("sie-nonstaff@example.com", is_staff=False)
        self.company.users.add(non_staff)
        set_active_company(self.client, self.company)
        self.client.force_login(non_staff)

        response = self.client.get(f"{reverse('bookkeeping:sie_export')}?year={self.year_2026.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

    def test_sie_import_diagnostics_download_returns_json(self):
        sie_content = "\n".join(
            [
                "#TRANS 1930 100.00",
                '#VER "A" "1" 20260210 "Ny import"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
            ]
        )
        upload = SimpleUploadedFile("import.se", sie_content.encode("utf-8"), content_type="text/plain")

        import_response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}",
            {"sie_file": upload},
        )
        self.assertEqual(import_response.status_code, 302)

        diagnostics_response = self.client.get(
            f"{reverse('bookkeeping:sie_import_diagnostics_download')}?year={self.year_2026.pk}"
        )
        self.assertEqual(diagnostics_response.status_code, 200)
        self.assertIn("application/json", diagnostics_response["Content-Type"])
        payload = json.loads(diagnostics_response.content.decode("utf-8"))
        self.assertEqual(payload["source"], "SIE")
        self.assertGreater(payload["count"], 0)

    def test_si_import_diagnostics_download_returns_json(self):
        session = self.client.session
        preview_key = f"sie_import_preview:{self.user.id}:{self.company.id}:{self.year_2026.id}"
        session[preview_key] = {
            "company_id": self.company.id,
            "year_id": self.year_2026.id,
            "verifications": [],
            "diagnostics": ["Rad 1: #TRANS hittades utan aktiv #VER."],
        }
        session.save()

        diagnostics_response = self.client.get(
            f"{reverse('bookkeeping:si_import_diagnostics_download')}?year={self.year_2026.pk}"
        )
        self.assertEqual(diagnostics_response.status_code, 200)
        self.assertIn("application/json", diagnostics_response["Content-Type"])
        payload = json.loads(diagnostics_response.content.decode("utf-8"))
        self.assertEqual(payload["source"], "SI")
        self.assertGreater(payload["count"], 0)


class SRUDiagnosticsTests(CompanyTestCase):
    user_email = "sru-user@example.com"
    user_fields = {"is_staff": True}
    company_name = "SRU Bolag AB"
    company_org_number = "556677-0002"

    def setUp(self):
        super().setUp()
        self.account_with_sru = create_account(
            self.company, "3010", "Försäljning", AccountClass.REVENUE, sru_code="3011"
        )
        self.account_without_sru = create_account(
            self.company,
            # 2000-2079 falls in a gap in the BAS->SRU range table (auto-fill on
            # save() leaves it blank), unlike most 1000-8999 numbers.
            "2000",
            "Eget kapital, ospecificerat",
            AccountClass.EQUITY_LIABILITY,
            sru_code="",
        )

        txn = Transaction.objects.create(
            accounting_year=self.year,
            date="2026-06-01",
            description="SRU underlag",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.account_without_sru, debit=Decimal("1000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.account_with_sru, debit=Decimal("0.00"), credit=Decimal("1000.00")
        )

    def test_sru_download_is_blocked_when_preflight_has_errors(self):
        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('bookkeeping:sru_report')}?year={self.year.pk}")

    def test_sru_download_is_blocked_for_non_finance_admin(self):
        non_staff = create_user("sru-nonstaff@example.com", is_staff=False)
        self.company.users.add(non_staff)
        set_active_company(self.client, self.company)
        self.client.force_login(non_staff)

        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

    def test_sru_download_succeeds_when_preflight_passes(self):
        self.account_without_sru.sru_code = "7301"
        self.account_without_sru.save(update_fields=["sru_code"])

        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response["Content-Type"])
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(sorted(zf.namelist()), ["blanketter.sru", "info.sru"])

    def test_sru_download_is_blocked_when_the_org_number_is_missing(self):
        """org_number is blank=True, and it goes straight into #ORGNR and #IDENTITET.

        Emitting the file with those empty produces a filing Skatteverket rejects, so
        the export has to refuse rather than ship one.
        """
        self.account_without_sru.sru_code = "7301"
        self.account_without_sru.save(update_fields=["sru_code"])
        self.company.org_number = ""
        self.company.save(update_fields=["org_number"])

        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('bookkeeping:sru_report')}?year={self.year.pk}")

    def test_sru_preflight_report_downloads_json(self):
        response = self.client.get(
            f"{reverse('bookkeeping:sru_preflight_report_download')}?year={self.year.pk}&format=json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response["Content-Type"])
        payload = json.loads(response.content.decode("utf-8"))
        self.assertGreater(payload["error_count"], 0)
        self.assertTrue(any(row["type"] == "missing_sru_code" for row in payload["findings"]))

    def test_sru_preflight_report_downloads_csv(self):
        response = self.client.get(
            f"{reverse('bookkeeping:sru_preflight_report_download')}?year={self.year.pk}&format=csv"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        csv_text = response.content.decode("utf-8")
        self.assertIn("missing_sru_code", csv_text)
        self.assertIn("2000", csv_text)


class SieOpeningBalanceImportTests(SIEImportTests):
    def _import(self, lines):
        upload = SimpleUploadedFile("import.se", "\n".join(lines).encode("utf-8"), content_type="text/plain")
        response = self.client.post(
            f"{reverse('bookkeeping:sie_import')}?year={self.year_2026.pk}", {"sie_file": upload}
        )
        self.assertEqual(response.status_code, 302)
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_ib_rows_become_an_opening_balance_voucher(self):
        messages_ = self._import(
            [
                "#IB 0 1930 25000.00",
                "#IB 0 2440 -25000.00",
                '#VER "A" "1" 20260210 "Löpande"',
                "{",
                "#TRANS 1930 100.00",
                "#TRANS 2440 -100.00",
                "}",
                "#UB 0 1930 25100.00",
            ]
        )
        ib = Transaction.objects.get(accounting_year=self.year_2026, reference="IB")
        self.assertEqual(str(ib.date), "2026-01-01")
        self.assertEqual(ib.entries.get(account__number="1930").debit, Decimal("25000.00"))
        self.assertTrue(any("Ingående balanser: 2 konton" in m for m in messages_), messages_)
        self.assertFalse(any("Kontrollsumma" in m for m in messages_), messages_)

    def test_ib_only_file_is_accepted(self):
        self._import(["#IB 0 1930 500.00", "#IB 0 2081 -500.00"])
        self.assertTrue(Transaction.objects.filter(accounting_year=self.year_2026, reference="IB").exists())

    def test_unbalanced_ib_rows_are_skipped_with_a_message(self):
        messages_ = self._import(
            ["#IB 0 1930 500.00", '#VER "A" "1" 20260210 "x"', "{", "#TRANS 1930 1.00", "#TRANS 2440 -1.00", "}"]
        )
        self.assertFalse(Transaction.objects.filter(reference="IB").exists())
        self.assertTrue(any("balanserar inte" in m for m in messages_), messages_)

    def test_ib_is_skipped_when_earlier_bookkeeping_exists(self):
        txn = Transaction.objects.create(
            accounting_year=self.year_2025, date="2025-06-01", description="Gammal", created_by=self.user
        )
        account = Account.objects.get(company=self.company, number="1930")
        JournalEntry.objects.create(transaction=txn, account=account, debit=Decimal("1.00"), credit=Decimal("0.00"))
        JournalEntry.objects.create(
            transaction=txn,
            account=Account.objects.get(company=self.company, number="2440"),
            debit=Decimal("0.00"),
            credit=Decimal("1.00"),
        )
        messages_ = self._import(["#IB 0 1930 500.00", "#IB 0 2440 -500.00"])
        self.assertFalse(Transaction.objects.filter(reference="IB").exists())
        self.assertTrue(any("redan bokföring före" in m for m in messages_), messages_)

    def test_sie4_export_transliterates_characters_outside_cp437(self):
        txn = Transaction.objects.create(
            accounting_year=self.year_2026,
            date="2026-02-13",
            description="Insättning – ägarens kapital",
            created_by=self.user,
        )
        account_1930 = Account.objects.get(company=self.company, number="1930")
        account_2440 = Account.objects.get(company=self.company, number="2440")
        JournalEntry.objects.create(
            transaction=txn, account=account_1930, debit=Decimal("1.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=account_2440, debit=Decimal("0.00"), credit=Decimal("1.00")
        )

        response = self.client.get(f"{reverse('bookkeeping:sie_export')}?year={self.year_2026.pk}")

        content = response.content.decode("cp437")
        self.assertIn("Insättning - ägarens kapital", content)
        self.assertNotIn("?", content)
