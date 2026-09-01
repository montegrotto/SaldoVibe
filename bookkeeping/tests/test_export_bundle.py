import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from attachments.models import TransactionAttachment
from banking.models import BankAccount, BankTransaction
from bookkeeping.export_bundle import (
    bank_transaction_counts,
    build_agi_report_files,
    build_bank_transaction_files,
    build_payslip_files,
    build_reports_files,
    build_vat_report_files,
    compute_account_specifications,
    document_bucket_querysets,
    generate_export_bundle,
    overlapping_accounting_years,
    reports_counts,
    run_export_job,
    start_export_job,
)
from bookkeeping.models import AccountClass, ExportJob, JournalEntry, Transaction
from payroll.models import Employee, PayrollRun, SalaryRecord
from saldovibe.testing import (
    CompanyTestCase,
    create_account,
    create_accounting_year,
    create_company,
    create_user,
    set_active_company,
)
from supplier_invoices.models import SupplierInvoice
from vat.models import VatCloseSnapshot


class OverlappingAccountingYearsTests(CompanyTestCase):
    user_email = "export-years@example.com"
    company_name = "Export År AB"
    company_org_number = "556001-0001"
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.year_2024 = create_accounting_year(self.company, "2024-01-01", "2024-12-31")
        self.year_2025 = create_accounting_year(self.company, "2025-01-01", "2025-12-31")

    def test_finds_years_touching_a_multi_year_range(self):
        years = list(overlapping_accounting_years(self.company, date(2024, 6, 1), date(2025, 6, 1)))
        self.assertEqual(years, [self.year_2024, self.year_2025])

    def test_finds_single_year_for_range_inside_it(self):
        years = list(overlapping_accounting_years(self.company, date(2024, 3, 1), date(2024, 4, 1)))
        self.assertEqual(years, [self.year_2024])

    def test_excludes_years_entirely_outside_range(self):
        years = list(overlapping_accounting_years(self.company, date(2026, 1, 1), date(2026, 12, 31)))
        self.assertEqual(years, [])


class AccountSpecificationArithmeticTests(CompanyTestCase):
    user_email = "export-spec@example.com"
    company_name = "Export Spec AB"
    company_org_number = "556002-0002"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.equity_account = create_account(self.company, "2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY)

        def post(txn_date, description, debit, credit):
            txn = Transaction.objects.create(
                accounting_year=self.year, date=txn_date, description=description, created_by=self.user
            )
            JournalEntry.objects.create(transaction=txn, account=self.bank_account, debit=debit, credit=Decimal("0.00"))
            JournalEntry.objects.create(
                transaction=txn, account=self.equity_account, debit=Decimal("0.00"), credit=credit
            )
            return txn

        post("2025-01-10", "Före perioden", Decimal("1000.00"), Decimal("1000.00"))
        self.in_range_txn = post("2025-06-15", "Inom perioden", Decimal("500.00"), Decimal("500.00"))
        post("2025-09-01", "Efter perioden", Decimal("2000.00"), Decimal("2000.00"))

    def test_opening_balance_includes_entries_before_range_only(self):
        specs = compute_account_specifications(self.company, date(2025, 6, 1), date(2025, 6, 30))
        bank_spec = next(spec for spec in specs if spec["account"] == self.bank_account)
        self.assertEqual(bank_spec["opening_balance"], Decimal("1000.00"))

    def test_running_and_closing_balance_reflect_only_postings_in_range(self):
        specs = compute_account_specifications(self.company, date(2025, 6, 1), date(2025, 6, 30))
        bank_spec = next(spec for spec in specs if spec["account"] == self.bank_account)
        self.assertEqual(len(bank_spec["postings"]), 1)
        self.assertEqual(bank_spec["postings"][0]["balance"], Decimal("1500.00"))
        self.assertEqual(bank_spec["closing_balance"], Decimal("1500.00"))

    def test_account_with_no_activity_in_range_is_excluded(self):
        idle_account = create_account(self.company, "1940", "Sparkonto", AccountClass.ASSET)
        specs = compute_account_specifications(self.company, date(2025, 6, 1), date(2025, 6, 30))
        self.assertNotIn(idle_account, [spec["account"] for spec in specs])


class DocumentBucketingTests(CompanyTestCase):
    user_email = "export-docs@example.com"
    company_name = "Export Bilagor AB"
    company_org_number = "556003-0003"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.expense_account = create_account(self.company, "6540", "IT-tjänster", AccountClass.OTHER_EXTERNAL_2)
        self.payable_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)
        self.period_start = date(2025, 6, 1)
        self.period_end = date(2025, 6, 30)

    def _attachment(self, filename="kvitto.pdf"):
        return TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile(filename, b"%PDF-1.4 test", content_type="application/pdf"),
        )

    def test_booked_verification_attachment_in_range_is_booked(self):
        txn = Transaction.objects.create(
            accounting_year=self.year, date=date(2025, 6, 15), description="Kontorsmaterial", created_by=self.user
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.expense_account, debit=Decimal("100.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.bank_account, debit=Decimal("0.00"), credit=Decimal("100.00")
        )
        attachment = self._attachment()
        txn.attachments.add(attachment)

        booked_qs, not_booked_qs = document_bucket_querysets(self.company, self.period_start, self.period_end)
        self.assertIn(attachment, booked_qs)
        self.assertNotIn(attachment, not_booked_qs)

    def test_booked_verification_attachment_outside_range_is_excluded_entirely(self):
        txn = Transaction.objects.create(
            accounting_year=self.year, date=date(2025, 1, 5), description="Kontorsmaterial", created_by=self.user
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.expense_account, debit=Decimal("100.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.bank_account, debit=Decimal("0.00"), credit=Decimal("100.00")
        )
        attachment = self._attachment()
        txn.attachments.add(attachment)
        # Re-uploaded/duplicated later but still points at a January voucher - not part of a June export.
        attachment.uploaded_at = datetime(2025, 6, 10, 10, 0, tzinfo=dt_timezone.utc)
        attachment.save(update_fields=["uploaded_at"])

        booked_qs, not_booked_qs = document_bucket_querysets(self.company, self.period_start, self.period_end)
        self.assertNotIn(attachment, booked_qs)
        self.assertNotIn(attachment, not_booked_qs)

    def test_unlinked_attachment_uploaded_in_range_is_not_booked(self):
        attachment = self._attachment()
        attachment.uploaded_at = datetime(2025, 6, 10, 10, 0, tzinfo=dt_timezone.utc)
        attachment.save(update_fields=["uploaded_at"])

        booked_qs, not_booked_qs = document_bucket_querysets(self.company, self.period_start, self.period_end)
        self.assertIn(attachment, not_booked_qs)
        self.assertNotIn(attachment, booked_qs)

    def test_not_yet_registered_supplier_invoice_attachment_is_not_booked(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier_name="Kontorsleverantören",
            invoice_date=date(2025, 6, 5),
            due_date=date(2025, 7, 5),
            payable_account=self.payable_account,
            total_amount=Decimal("500.00"),
        )
        attachment = self._attachment("faktura.pdf")
        invoice.attachments.add(attachment)
        attachment.uploaded_at = datetime(2025, 6, 5, 9, 0, tzinfo=dt_timezone.utc)
        attachment.save(update_fields=["uploaded_at"])

        booked_qs, not_booked_qs = document_bucket_querysets(self.company, self.period_start, self.period_end)
        self.assertIn(attachment, not_booked_qs)
        self.assertNotIn(attachment, booked_qs)


class BankTransactionBundleTests(CompanyTestCase):
    user_email = "export-bank@example.com"
    company_name = "Export Bank AB"
    company_org_number = "556010-0010"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        bank_gl_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.bank_source = BankAccount.objects.create(
            company=self.company,
            name="Företagsbanken",
            account_type="bank",
            bookkeeping_account=bank_gl_account,
        )
        BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date=date(2025, 6, 10),
            description="Kortköp",
            amount=Decimal("-250.00"),
            external_id="tx-1",
            is_booked=True,
        )
        BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date=date(2025, 6, 12),
            description="Insättning",
            amount=Decimal("500.00"),
            external_id="tx-2",
        )
        BankTransaction.objects.create(
            company=self.company,
            bank_account=self.bank_source,
            date=date(2025, 1, 5),
            description="Utanför perioden",
            amount=Decimal("100.00"),
            external_id="tx-3",
        )

    def test_csv_contains_only_transactions_in_range(self):
        files = build_bank_transaction_files(self.company, date(2025, 6, 1), date(2025, 6, 30))
        csv_file = next(f for f in files if f.path.endswith(".csv"))
        content = csv_file.content.decode("utf-8-sig")
        self.assertIn("Kortköp", content)
        self.assertIn("Insättning", content)
        self.assertNotIn("Utanför perioden", content)

    def test_booked_column_reflects_status(self):
        files = build_bank_transaction_files(self.company, date(2025, 6, 1), date(2025, 6, 30))
        csv_file = next(f for f in files if f.path.endswith(".csv"))
        content = csv_file.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(content), delimiter=";"))
        by_description = {row[1]: row for row in rows[1:]}
        self.assertEqual(by_description["Kortköp"][4], "Ja")
        self.assertEqual(by_description["Insättning"][4], "Nej")

    def test_counts_summary_matches_the_period(self):
        counts = bank_transaction_counts(self.company, date(2025, 6, 1), date(2025, 6, 30))
        self.assertEqual(counts["account_count"], 1)
        self.assertEqual(counts["transaction_count"], 2)
        self.assertEqual(counts["not_booked_count"], 1)

    def test_bank_account_with_no_activity_in_range_produces_no_csv(self):
        files = build_bank_transaction_files(self.company, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([f.path for f in files], ["banktransaktioner/index.html"])


class ExportBundleActionPermissionTests(CompanyTestCase):
    user_email = "export-perm@example.com"
    company_name = "Export Behörighet AB"
    company_org_number = "556004-0004"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def _start(self):
        # Patched so no real background thread races the test's uncommitted transaction - a
        # thread opening its own DB connection can't see this job row until the transaction
        # commits, which TestCase never does (see the threading note on RunExportJobTests).
        with mock.patch("bookkeeping.export_bundle.threading.Thread"):
            return self.client.post(
                reverse("bookkeeping:export_bundle_start"),
                {"start_date": "2025-01-01", "end_date": "2025-12-31"},
            )

    def test_plain_user_is_blocked_from_starting_an_export(self):
        response = self._start()
        self.assertRedirects(response, reverse("bookkeeping:dashboard"))
        self.assertFalse(ExportJob.objects.exists())

    def test_finance_admin_group_member_can_start_an_export(self):
        group = Group.objects.create(name="finance_admin")
        self.user.groups.add(group)
        response = self._start()
        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))
        self.assertTrue(ExportJob.objects.filter(company=self.company).exists())

    def test_staff_user_can_start_an_export(self):
        staff_user = create_user("export-staff@example.com", is_staff=True)
        self.client.force_login(staff_user)
        self.company.users.add(staff_user)
        set_active_company(self.client, self.company)
        response = self._start()
        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))

    def test_superuser_can_start_an_export(self):
        superuser = create_user("export-super@example.com", is_superuser=True, is_staff=True)
        self.client.force_login(superuser)
        self.company.users.add(superuser)
        set_active_company(self.client, self.company)
        response = self._start()
        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))

    def test_plain_user_is_blocked_from_downloading(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )
        response = self.client.get(reverse("bookkeeping:export_bundle_download", args=[job.pk]))
        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

    def test_plain_user_is_blocked_from_deleting(self):
        job = ExportJob.objects.create(company=self.company, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        response = self.client.post(reverse("bookkeeping:export_bundle_delete", args=[job.pk]))
        self.assertRedirects(response, reverse("bookkeeping:dashboard"))
        self.assertTrue(ExportJob.objects.filter(pk=job.pk).exists())


class ExportBundleZipStructureTests(CompanyTestCase):
    user_email = "export-zip@example.com"
    company_name = "Export Zip AB"
    company_org_number = "556005-0005"
    company_fields = {}
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        group = Group.objects.create(name="finance_admin")
        self.user.groups.add(group)

        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.equity_account = create_account(self.company, "2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY)
        txn = Transaction.objects.create(
            accounting_year=self.year, date=date(2025, 3, 1), description="Startkapital", created_by=self.user
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.bank_account, debit=Decimal("1000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("1000.00")
        )
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("underlag.pdf", b"%PDF-1.4 underlag", content_type="application/pdf"),
        )
        txn.attachments.add(attachment)

        bank_source = BankAccount.objects.create(
            company=self.company,
            name="Företagsbanken",
            account_type="bank",
            bookkeeping_account=self.bank_account,
        )
        BankTransaction.objects.create(
            company=self.company,
            bank_account=bank_source,
            date=date(2025, 3, 1),
            description="Startkapital insatt",
            amount=Decimal("1000.00"),
            external_id="tx-startkapital",
        )

    def test_bundle_has_expected_top_level_structure(self):
        payload = generate_export_bundle(self.company, date(2025, 1, 1), date(2025, 12, 31))
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = set(zf.namelist())

        self.assertIn("index.html", names)
        self.assertIn("manifest.json", names)
        self.assertIn("css/style.css", names)
        self.assertTrue(any(name.startswith("bokforingsdata/") for name in names))
        self.assertTrue(any(name.startswith("kontospecifikationer/") for name in names))
        self.assertTrue(any(name.startswith("bilagor/bokforda/") for name in names))
        self.assertTrue(any(name.startswith("banktransaktioner/") and name.endswith(".csv") for name in names))
        self.assertIn("rapporter/index.html", names)
        self.assertIn("rapporter/moms/index.html", names)
        self.assertIn("rapporter/agi/index.html", names)
        self.assertIn("rapporter/lonebesked/index.html", names)

    def test_manifest_hashes_match_the_archived_files(self):
        payload = generate_export_bundle(self.company, date(2025, 1, 1), date(2025, 12, 31))
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["schema"], "saldovibe.export.v1")
            self.assertEqual(manifest["company"]["name"], self.company.name)

            for entry in manifest["files"]:
                archived_bytes = zf.read(entry["path"])
                self.assertEqual(hashlib.sha256(archived_bytes).hexdigest(), entry["sha256"])

    def test_download_view_streams_the_completed_jobs_file(self):
        job = ExportJob.objects.create(company=self.company, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        run_export_job(job.pk)

        response = self.client.get(reverse("bookkeeping:export_bundle_download", args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        payload = b"".join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            self.assertIsNone(zf.testzip())


class RunExportJobTests(CompanyTestCase):
    """`run_export_job` is exercised as a plain synchronous call, not via a real background
    thread: TestCase wraps each test in an uncommitted transaction, and a genuinely separate OS
    thread opens its own DB connection that can't see that uncommitted data - so real cross-thread
    completion can't be asserted reliably here. Tests that go through the view/`start_export_job`
    instead patch `threading.Thread` to a no-op and only assert job *creation*, not completion.
    """

    user_email = "export-run@example.com"
    company_name = "Export Kör AB"
    company_org_number = "556006-0006"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def test_completes_and_persists_the_zip(self):
        job = ExportJob.objects.create(company=self.company, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))

        run_export_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.Status.COMPLETED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.completed_at)
        self.assertTrue(job.file)
        with zipfile.ZipFile(io.BytesIO(job.file.read())) as zf:
            self.assertIn("manifest.json", zf.namelist())
        job.file.delete(save=False)

    def test_marks_failed_with_error_message_when_generation_raises(self):
        job = ExportJob.objects.create(company=self.company, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))

        with mock.patch("bookkeeping.export_bundle.generate_export_bundle", side_effect=RuntimeError("kaboom")):
            run_export_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.Status.FAILED)
        self.assertIn("kaboom", job.error_message)
        self.assertFalse(job.file)


class ExportBundleJobViewTests(CompanyTestCase):
    user_email = "export-jobs@example.com"
    company_name = "Export Jobb AB"
    company_org_number = "556007-0007"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        self.user.groups.add(Group.objects.create(name="finance_admin"))

    def test_start_creates_a_pending_job_and_redirects_without_waiting_for_completion(self):
        # Patched so no real background thread races the test's uncommitted transaction - see
        # the threading note on RunExportJobTests.
        with mock.patch("bookkeeping.export_bundle.threading.Thread"):
            response = self.client.post(
                reverse("bookkeeping:export_bundle_start"),
                {"start_date": "2025-01-01", "end_date": "2025-12-31"},
            )
        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))
        job = ExportJob.objects.get(company=self.company)
        self.assertEqual(job.start_date, date(2025, 1, 1))
        self.assertEqual(job.end_date, date(2025, 12, 31))
        self.assertEqual(job.requested_by, self.user)

    def test_start_export_job_returns_a_pending_job_immediately(self):
        # Doesn't wait on the spawned background thread - completion is covered by the
        # synchronous RunExportJobTests instead (see the threading note below).
        with mock.patch("bookkeeping.export_bundle.threading.Thread"):
            job = start_export_job(self.company, date(2025, 1, 1), date(2025, 12, 31), requested_by=self.user)
        self.assertEqual(job.status, ExportJob.Status.PENDING)

    def test_download_blocked_when_job_not_yet_completed(self):
        job = ExportJob.objects.create(company=self.company, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        response = self.client.get(reverse("bookkeeping:export_bundle_download", args=[job.pk]))
        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))

    def test_download_404s_for_a_job_belonging_to_another_company(self):
        other_owner = create_user("export-other-owner@example.com")
        other_company = create_company("Export Annat Bolag AB", "556008-0008", users=[other_owner])
        job = ExportJob.objects.create(
            company=other_company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )
        response = self.client.get(reverse("bookkeeping:export_bundle_download", args=[job.pk]))
        self.assertEqual(response.status_code, 404)
        job.file.delete(save=False)

    def test_delete_removes_the_file_and_the_row(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )
        file_storage = job.file.storage
        file_name = job.file.name
        self.assertTrue(file_storage.exists(file_name))

        response = self.client.post(reverse("bookkeeping:export_bundle_delete", args=[job.pk]))

        self.assertRedirects(response, reverse("bookkeeping:export_bundle"))
        self.assertFalse(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertFalse(file_storage.exists(file_name))


class ExportPageSeenAtAndNotificationTests(CompanyTestCase):
    user_email = "export-seen@example.com"
    company_name = "Export Sedd AB"
    company_org_number = "556009-0009"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def test_visiting_the_export_page_marks_completed_jobs_seen(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )
        self.assertIsNone(job.seen_at)

        self.client.get(reverse("bookkeeping:export_bundle"))

        job.refresh_from_db()
        self.assertIsNotNone(job.seen_at)
        job.file.delete(save=False)

    def test_topbar_shows_ready_count_until_export_page_is_visited(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )

        other_page = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(other_page.context["export_jobs_ready_count"], 1)
        self.assertTrue(other_page.context["topbar_alert_has_alert"])

        self.client.get(reverse("bookkeeping:export_bundle"))

        other_page_again = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(other_page_again.context["export_jobs_ready_count"], 0)
        job.file.delete(save=False)


class TopbarAlertStatusPollTests(CompanyTestCase):
    """A user sitting on one page (not reloading, not visiting the export page) would otherwise
    never see a background export finish - the topbar bell is only computed by page render, and
    nothing there forces one. `bookkeeping:topbar_alert_status` is what `base.html` polls instead;
    these tests cover the JSON contract the polling JS relies on."""

    user_email = "export-poll@example.com"
    company_name = "Export Poll AB"
    company_org_number = "556011-0011"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def test_reports_no_alert_when_nothing_is_pending(self):
        response = self.client.get(reverse("bookkeeping:topbar_alert_status"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["has_any"])
        self.assertNotIn("export-jobs-ready-alert-item", data["html"])

    def test_reports_a_completed_export_without_ever_visiting_the_export_page(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )

        response = self.client.get(reverse("bookkeeping:topbar_alert_status"))

        data = response.json()
        self.assertTrue(data["has_any"])
        self.assertIn("export-jobs-ready-alert-item", data["html"])
        self.assertIn("Exportpaket redo att laddas ner (1)", data["html"])
        job.file.delete(save=False)

    def test_token_changes_when_a_job_completes(self):
        before = self.client.get(reverse("bookkeeping:topbar_alert_status")).json()["token"]

        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )

        after = self.client.get(reverse("bookkeeping:topbar_alert_status")).json()["token"]
        self.assertNotEqual(before, after)
        job.file.delete(save=False)

    def test_stops_reporting_once_the_export_page_has_been_visited(self):
        job = ExportJob.objects.create(
            company=self.company,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            status=ExportJob.Status.COMPLETED,
            file=SimpleUploadedFile("export.zip", b"PK\x03\x04fake", content_type="application/zip"),
        )

        self.client.get(reverse("bookkeeping:export_bundle"))

        data = self.client.get(reverse("bookkeeping:topbar_alert_status")).json()
        self.assertFalse(data["has_any"])
        job.file.delete(save=False)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("bookkeeping:topbar_alert_status"))
        self.assertEqual(response.status_code, 302)


class ReportsSectionTests(CompanyTestCase):
    """rapporter/: moms/AGI/lönebesked only for periods actually closed/reported - never
    regenerated on the fly for an ongoing period, per the Phase 3 rule in export_bundle.py."""

    user_email = "export-reports@example.com"
    company_name = "Export Rapporter AB"
    company_org_number = "556006-0006"
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        self.employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
        )

        self.in_range_snapshot = VatCloseSnapshot.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 3, 31),
            vat_boxes={"05": "10000.00", "10": "2500.00", "48": "500.00", "49": "2000.00"},
            source_transaction_ids=[],
            source_fingerprint="fp-in-range",
        )
        VatCloseSnapshot.objects.create(
            company=self.company,
            accounting_year=create_accounting_year(self.company, "2024-01-01", "2024-12-31"),
            period_start=date(2024, 1, 1),
            period_end=date(2024, 3, 31),
            vat_boxes={"05": "1.00"},
            source_transaction_ids=[],
            source_fingerprint="fp-out-of-range",
        )

        with mock.patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("9500.00"), "reference": "api-ref-1"},
        ):
            self.reported_run = PayrollRun.objects.create(
                company=self.company,
                period_year=2025,
                period_month=3,
                payment_date=date(2025, 3, 25),
                is_finished=True,
                is_reported_to_skatteverket=True,
                reported_at=timezone.now(),
            )
            SalaryRecord.objects.create(
                payroll_run=self.reported_run,
                employee=self.employee,
                gross_salary=Decimal("40000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )

            self.unreported_run = PayrollRun.objects.create(
                company=self.company,
                period_year=2025,
                period_month=4,
                payment_date=date(2025, 4, 25),
                is_finished=True,
            )
            SalaryRecord.objects.create(
                payroll_run=self.unreported_run,
                employee=self.employee,
                gross_salary=Decimal("40000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )

    def test_reports_counts_only_cover_the_chosen_period(self):
        counts = reports_counts(self.company, date(2025, 1, 1), date(2025, 12, 31))
        self.assertEqual(counts["vat_period_count"], 1)
        self.assertEqual(counts["agi_run_count"], 1)
        self.assertEqual(counts["payslip_count"], 2)

    def test_vat_report_excludes_snapshot_outside_range(self):
        files = build_vat_report_files(self.company, date(2025, 1, 1), date(2025, 12, 31))
        pdf_paths = [f.path for f in files if f.path.endswith(".pdf")]
        self.assertEqual(len(pdf_paths), 1)
        self.assertIn("rapporter/moms/index.html", [f.path for f in files])

    def test_agi_report_excludes_unreported_run(self):
        files = build_agi_report_files(self.company, date(2025, 1, 1), date(2025, 12, 31))
        pdf_paths = [f.path for f in files if f.path.endswith(".pdf")]
        self.assertEqual(
            pdf_paths,
            [f"rapporter/agi/AGI {self.reported_run.period_year}-{self.reported_run.period_month:02d}.pdf"],
        )

    def test_payslip_covers_every_finished_run_in_range(self):
        files = build_payslip_files(self.company, date(2025, 1, 1), date(2025, 12, 31))
        pdf_paths = [f.path for f in files if f.path.endswith(".pdf")]
        self.assertEqual(len(pdf_paths), 2)

    def test_reports_bundle_has_a_top_level_index(self):
        files = build_reports_files(self.company, date(2025, 1, 1), date(2025, 12, 31))
        self.assertIn("rapporter/index.html", [f.path for f in files])
