import shutil
import subprocess
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookkeeping.models import (
    AccountClass,
    JournalEntry,
    Transaction,
    TransactionSource,
    VoucherSeriesRule,
)
from invoicing.models import Article, Customer, Invoice
from saldovibe.testing import CompanyTestCase, create_account, create_accounts, create_company, create_user
from supplier_invoices.models import Supplier, SupplierInvoice

from .context import audit_user
from .models import AuditChainAnchor, AuditLogEntry
from .services import calculate_audit_entry_hash
from .timestamping import (
    TimestampRequestError,
    TimestampVerificationError,
    get_asserted_time,
    request_timestamp,
    verify_timestamp_token,
)


class AuditLogTests(CompanyTestCase):
    # The ledger records who made each change, so the name matters here.
    user_email = "audit@example.com"
    user_fields = {"first_name": "Ada", "last_name": "Audit"}
    company_name = "Audit AB"
    company_org_number = "556677-1122"

    def setUp(self):
        super().setUp()
        accounts = create_accounts(
            self.company,
            [
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("3010", "Försäljning", AccountClass.REVENUE),
                ("4010", "Varuinköp", AccountClass.COST_OF_GOODS),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
                ("2640", "Ingående moms", AccountClass.ASSET),
            ],
        )
        self.cash_account = accounts["1930"]
        self.sales_account = accounts["3010"]
        self.expense_account = accounts["4010"]
        self.payable_account = accounts["2440"]
        self.vat_account = accounts["2640"]
        self.customer = Customer.objects.create(
            company=self.company,
            name="Kund AB",
            org_number="556688-9900",
            is_active=True,
        )
        self.article = Article.objects.create(
            company=self.company,
            article_number="A-1",
            name="Tjänst",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Leverantör AB",
            org_number="556699-0011",
            bankgiro="123-4567",
            is_active=True,
        )

    def test_account_update_creates_audit_entry_with_field_diff(self):
        start_count = AuditLogEntry.objects.count()

        response = self.client.post(
            reverse("bookkeeping:account_update", args=[self.cash_account.pk]),
            {
                "number": self.cash_account.number,
                "name": "Bankkonto huvud",
                "account_class": self.cash_account.account_class,
                "vat_field_code": "",
                "sru_code": "7201",
                "description": "Uppdaterat från test",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:account_list"))
        self.assertGreater(AuditLogEntry.objects.count(), start_count)

        entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.account",
            object_pk=str(self.cash_account.pk),
            action=AuditLogEntry.Action.UPDATE,
        ).latest("id")

        self.assertEqual(entry.actor, self.user)
        self.assertEqual(entry.changes["name"]["after"], "Bankkonto huvud")
        self.assertEqual(entry.changes["sru_code"]["after"], "7201")

    def test_company_filefield_update_is_serialized_as_string(self):
        self.company.company_icon = "company_icons/logo-test.png"
        self.company.save()

        entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.company",
            object_pk=str(self.company.pk),
            action=AuditLogEntry.Action.UPDATE,
        ).latest("id")

        self.assertEqual(entry.changes["company_icon"]["before"], "")
        self.assertEqual(entry.changes["company_icon"]["after"], "company_icons/logo-test.png")

    def test_transaction_create_logs_transaction_and_entries(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-30",
                "description": "Testverifikation",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": self.cash_account.pk,
                "entries-0-debit": "1250.00",
                "entries-0-credit": "0.00",
                "entries-0-description": "Debet",
                "entries-1-account": self.sales_account.pk,
                "entries-1-debit": "0.00",
                "entries-1-credit": "1250.00",
                "entries-1-description": "Kredit",
            },
        )

        self.assertRedirects(response, reverse("bookkeeping:transaction_list"))
        txn = Transaction.objects.get(description="Testverifikation", date="2026-06-30")

        self.assertTrue(
            AuditLogEntry.objects.filter(
                company=self.company,
                model_label="bookkeeping.transaction",
                object_pk=str(txn.pk),
                action=AuditLogEntry.Action.CREATE,
            ).exists()
        )
        self.assertEqual(
            AuditLogEntry.objects.filter(
                company=self.company,
                model_label="bookkeeping.journalentry",
                action=AuditLogEntry.Action.CREATE,
            ).count(),
            2,
        )

    def test_reconcile_detects_a_row_deleted_by_raw_sql(self):
        self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-30",
                "description": "Rå radering",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": self.cash_account.pk,
                "entries-0-debit": "1250.00",
                "entries-0-credit": "0.00",
                "entries-0-description": "Debet",
                "entries-1-account": self.sales_account.pk,
                "entries-1-debit": "0.00",
                "entries-1-credit": "1250.00",
                "entries-1-description": "Kredit",
            },
        )
        txn_pk = Transaction.objects.get(description="Rå radering").pk

        clean = StringIO()
        call_command("reconcile_audit_log", stdout=clean)
        self.assertIn("stämmer överens", clean.getvalue())

        # Delete straight in the DB, bypassing the ORM signals that would log it.
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {JournalEntry._meta.db_table} WHERE transaction_id = %s", [txn_pk])
            cursor.execute(f"DELETE FROM {Transaction._meta.db_table} WHERE id = %s", [txn_pk])

        tampered = StringIO()
        with self.assertRaises(SystemExit):
            call_command("reconcile_audit_log", stdout=tampered)
        self.assertIn(f"bookkeeping.transaction {txn_pk}", tampered.getvalue())

    def test_invoice_create_logs_customer_invoice(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "ocr_code": "1234567890",
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Ref",
                "notes": "Notering",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Arbete",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )

        self.assertRedirects(response, reverse("invoicing:invoice_list"))
        invoice = Invoice.objects.get(company=self.company)

        self.assertTrue(
            AuditLogEntry.objects.filter(
                company=self.company,
                model_label="invoicing.invoice",
                object_pk=str(invoice.pk),
                action=AuditLogEntry.Action.CREATE,
            ).exists()
        )
        invoice_entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="invoicing.invoice",
            object_pk=str(invoice.pk),
            action=AuditLogEntry.Action.CREATE,
        ).latest("id")
        self.assertEqual(invoice_entry.changes["customer"]["after"], "Kund AB")
        self.assertEqual(
            AuditLogEntry.objects.filter(
                company=self.company,
                model_label="invoicing.invoiceline",
                action=AuditLogEntry.Action.CREATE,
            ).count(),
            1,
        )

    def test_supplier_invoice_register_and_payment_are_logged(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="LEV-2026-1",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )
        invoice.cost_lines.create(expense_account=self.expense_account, debit=Decimal("400.00"))

        register_response = self.client.post(reverse("supplier_invoices:invoice_register", args=[invoice.pk]))
        self.assertRedirects(register_response, reverse("supplier_invoices:invoice_list"))

        pay_response = self.client.post(
            reverse("supplier_invoices:invoice_register_payment", args=[invoice.pk]),
            {
                "payment_date": "2026-06-30",
                "amount": "500.00",
                "payment_account": str(self.cash_account.pk),
            },
        )
        self.assertRedirects(pay_response, reverse("supplier_invoices:invoice_list"))

        invoice.refresh_from_db()
        update_entries = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="supplier_invoices.supplierinvoice",
            object_pk=str(invoice.pk),
            action=AuditLogEntry.Action.UPDATE,
        )

        self.assertTrue(update_entries.filter(changes__is_registered__after=True).exists())
        self.assertTrue(update_entries.filter(changes__is_paid__after=True).exists())

    def test_audit_entries_are_hash_chained(self):
        self.cash_account.name = "Företagskonto uppdaterat"
        self.cash_account.save(update_fields=["name"])

        first_two = list(AuditLogEntry.objects.filter(company=self.company).order_by("id")[:2])
        self.assertEqual(len(first_two), 2)

        first_entry, second_entry = first_two
        self.assertEqual(first_entry.prev_hash, "")
        self.assertTrue(first_entry.entry_hash)
        self.assertEqual(second_entry.prev_hash, first_entry.entry_hash)

        calculated_hash = calculate_audit_entry_hash(second_entry, second_entry.prev_hash)
        self.assertEqual(second_entry.entry_hash, calculated_hash)

    def test_chains_are_per_company_even_when_writes_interleave(self):
        other_company = create_company("Interleave AB", "556700-9911")
        other_account = create_account(other_company, "1930", "Företagskonto", AccountClass.ASSET)

        self.cash_account.name = "A första"
        self.cash_account.save(update_fields=["name"])
        other_account.name = "B emellan"
        other_account.save(update_fields=["name"])
        self.cash_account.name = "A andra"
        self.cash_account.save(update_fields=["name"])

        own_chain = list(AuditLogEntry.objects.filter(chain_key=str(self.company.pk)).order_by("id"))
        other_chain = list(AuditLogEntry.objects.filter(chain_key=str(other_company.pk)).order_by("id"))

        # The other company's write really did land between the two own writes in
        # global id order - that's the interleaving that used to break the chain.
        self.assertLess(own_chain[-2].id, other_chain[-1].id)
        self.assertLess(other_chain[-1].id, own_chain[-1].id)

        for chain in (own_chain, other_chain):
            self.assertEqual(chain[0].prev_hash, "")
            for previous, entry in zip(chain, chain[1:]):
                self.assertEqual(entry.prev_hash, previous.entry_hash)

        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("giltig", out.getvalue())

    def test_legacy_global_chain_verifies_as_frozen_segment(self):
        # Entries from before per-company chains (hash_version=1) chain globally across
        # company boundaries and must keep verifying as their own frozen segment.
        prev_hash = ""
        legacy_entries = []
        for company_name in ("Audit AB", "Annat AB"):
            entry = AuditLogEntry.objects.create(
                action=AuditLogEntry.Action.CREATE,
                company_name=company_name,
                model_label="bookkeeping.account",
                model_name="Konto",
                object_pk="0",
                object_repr="Legacy",
                summary="Skapad: Konto Legacy",
                hash_version=1,
                prev_hash=prev_hash,
            )
            prev_hash = calculate_audit_entry_hash(entry, entry.prev_hash)
            AuditLogEntry.objects.filter(pk=entry.pk).update(entry_hash=prev_hash)
            legacy_entries.append(entry)

        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("giltig", out.getvalue())

        AuditLogEntry.objects.filter(pk=legacy_entries[0].pk).update(entry_hash="0" * 64)
        with self.assertRaises(SystemExit):
            call_command("verify_audit_chain", stdout=StringIO())

    def test_chain_stays_verifiable_after_company_deletion(self):
        doomed = create_company("Raderas AB", "556700-4455")
        doomed_key = str(doomed.pk)

        # Mirrors bookkeeping/views/companies.py::company_delete (the auto-created
        # Skattekonto BankAccount is PROTECT-guarded).
        doomed.bank_accounts.all().delete()
        doomed.delete()

        entries = AuditLogEntry.objects.filter(chain_key=doomed_key)
        self.assertTrue(entries.exists())
        # The company FK is nulled by SET_NULL, but chain_key keeps the chain identity.
        self.assertFalse(entries.filter(company__isnull=False).exists())

        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("giltig", out.getvalue())

    def test_voucher_series_rule_changes_are_audit_logged(self):
        # BFNAR 2013:2 punkt 9.6/9.16: systemdokumentationen/behandlingshistoriken ska visa
        # hur verifikationsserierna är indelade och när sådana förändringar gjorts.
        VoucherSeriesRule.seed_defaults_for_company(self.company)
        rule = VoucherSeriesRule.objects.get(company=self.company, source=TransactionSource.BANK)

        rule.series_code = "K"
        rule.save()

        entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.voucherseriesrule",
            object_pk=str(rule.pk),
            action=AuditLogEntry.Action.UPDATE,
        ).latest("id")

        self.assertEqual(entry.changes["series_code"]["before"], "B")
        self.assertEqual(entry.changes["series_code"]["after"], "K")

    def test_voucher_series_seeding_is_audit_logged(self):
        VoucherSeriesRule.seed_defaults_for_company(self.company)

        created = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.voucherseriesrule",
            action=AuditLogEntry.Action.CREATE,
        )
        self.assertEqual(created.count(), VoucherSeriesRule.objects.filter(company=self.company).count())

    def test_sensitive_field_change_between_two_secrets_is_still_logged(self):
        self.company.email_fetch_password = "hemligt-1"
        self.company.save(update_fields=["email_fetch_password"])
        self.company.email_fetch_password = "hemligt-2"
        self.company.save(update_fields=["email_fetch_password"])

        entry = AuditLogEntry.objects.filter(
            company=self.company,
            model_label="bookkeeping.company",
            action=AuditLogEntry.Action.UPDATE,
            changes__has_key="email_fetch_password",
        ).latest("id")

        change = entry.changes["email_fetch_password"]
        self.assertTrue(change["before"].startswith("[redacted:"))
        self.assertTrue(change["after"].startswith("[redacted:"))
        self.assertNotEqual(change["before"], change["after"])
        self.assertNotIn("hemligt", str(entry.changes))

    def test_payroll_models_are_audit_logged_with_redacted_personnummer(self):
        from payroll.models import Employee, PayrollRun, SalaryRecord

        with audit_user(self.user):
            employee = Employee.objects.create(
                company=self.company,
                first_name="Löna",
                last_name="Lönesson",
                personal_identity_number="199001019999",
                monthly_salary=Decimal("30000.00"),
            )
            run = PayrollRun.objects.create(
                company=self.company,
                period_year=2026,
                period_month=1,
                payment_date="2026-01-25",
                created_by=self.user,
            )
            record = SalaryRecord.objects.create(
                payroll_run=run,
                employee=employee,
                gross_salary=Decimal("30000.00"),
            )

        employee_entry = AuditLogEntry.objects.get(
            model_label="payroll.employee",
            object_pk=str(employee.pk),
            action=AuditLogEntry.Action.CREATE,
        )
        self.assertEqual(employee_entry.company_id, self.company.pk)
        self.assertTrue(employee_entry.changes["personal_identity_number"]["after"].startswith("[redacted"))

        # SalaryRecord resolves its company through the dotted payroll_run.company path.
        record_entry = AuditLogEntry.objects.get(
            model_label="payroll.salaryrecord",
            object_pk=str(record.pk),
            action=AuditLogEntry.Action.CREATE,
        )
        self.assertEqual(record_entry.company_id, self.company.pk)
        self.assertEqual(record_entry.metadata["parent"]["model_label"], "payroll.payrollrun")

    def test_fixed_asset_and_attachment_changes_are_audit_logged(self):
        from attachments.models import TransactionAttachment
        from fixed_assets.models import FixedAsset, FixedAssetType

        with audit_user(self.user):
            FixedAssetType.objects.create(
                company=self.company,
                key="maskiner",
                name="Maskiner",
                depreciation_expense_account=self.expense_account,
                accumulated_depreciation_account=self.payable_account,
            )
            asset = FixedAsset.objects.create(
                company=self.company,
                asset_type=FixedAsset.AssetType.MACHINERY,
                name="Svarv",
                acquisition_date="2026-01-01",
                depreciation_start_date="2026-01-01",
                acquisition_value=Decimal("120000.00"),
                useful_life_months=60,
            )
            attachment = TransactionAttachment.objects.create(
                company=self.company,
                uploaded_by=self.user,
            )

        for model_label, pk in [
            ("fixed_assets.fixedasset", asset.pk),
            ("attachments.transactionattachment", attachment.pk),
        ]:
            entry = AuditLogEntry.objects.get(
                model_label=model_label,
                object_pk=str(pk),
                action=AuditLogEntry.Action.CREATE,
            )
            self.assertEqual(entry.company_id, self.company.pk)

    def test_reseal_audit_chain_dry_run_does_not_modify_rows(self):
        self.cash_account.name = "Konto reseal dry-run"
        self.cash_account.save(update_fields=["name"])

        entry = AuditLogEntry.objects.filter(company=self.company).order_by("id").first()
        entry.entry_hash = "broken"
        entry.save(update_fields=["entry_hash"])

        out = StringIO()
        call_command("reseal_audit_chain", stdout=out)

        entry.refresh_from_db()
        self.assertEqual(entry.entry_hash, "broken")
        self.assertIn("DRY-RUN", out.getvalue())

    def test_reseal_audit_chain_apply_repairs_rows(self):
        self.cash_account.name = "Konto reseal apply"
        self.cash_account.save(update_fields=["name"])

        first_entry = AuditLogEntry.objects.filter(company=self.company).order_by("id").first()
        first_entry.entry_hash = "broken"
        first_entry.save(update_fields=["entry_hash"])

        out = StringIO()
        call_command("reseal_audit_chain", "--apply", stdout=out)

        expected_prev = ""
        for entry in AuditLogEntry.objects.filter(company=self.company).order_by("id"):
            expected_hash = calculate_audit_entry_hash(entry, expected_prev)
            self.assertEqual(entry.prev_hash, expected_prev)
            self.assertEqual(entry.entry_hash, expected_hash)
            expected_prev = expected_hash

        self.assertIn("APPLY", out.getvalue())

    def test_reseal_start_id_seed_is_scoped_per_company(self):
        # Create one additional entry for the primary company.
        self.cash_account.name = "Company one before start"
        self.cash_account.save(update_fields=["name"])
        company_one_previous = AuditLogEntry.objects.filter(company=self.company).order_by("-id").first()

        # Create an entry for another company so the global previous id differs.
        other_company = create_company("Other AB", "556700-2233")
        other_entry = AuditLogEntry.objects.filter(company=other_company).order_by("-id").first()

        # Create the first entry that will be resealed for company one.
        self.cash_account.name = "Company one start"
        self.cash_account.save(update_fields=["name"])
        start_entry = AuditLogEntry.objects.filter(company=self.company).order_by("-id").first()

        # Corrupt start entry to force reseal.
        start_entry.prev_hash = "wrong-prev"
        start_entry.entry_hash = "wrong-hash"
        start_entry.save(update_fields=["prev_hash", "entry_hash"])

        call_command(
            "reseal_audit_chain",
            "--company-id",
            str(self.company.id),
            "--start-id",
            str(start_entry.id),
            "--apply",
        )

        start_entry.refresh_from_db()
        self.assertEqual(start_entry.prev_hash, company_one_previous.entry_hash)
        self.assertNotEqual(start_entry.prev_hash, other_entry.entry_hash)

    def test_report_view_shows_company_entries(self):
        self.cash_account.name = "Nytt kontonamn"
        self.cash_account.save()

        response = self.client.get(reverse("auditlog:report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Händelselogg")
        self.assertContains(response, "Nytt kontonamn")

    def test_report_view_uses_single_value_column_for_creates(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "ocr_code": "1234567890",
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Ref",
                "notes": "Notering",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Arbete",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )
        self.assertRedirects(response, reverse("invoicing:invoice_list"))

        report_response = self.client.get(
            reverse("auditlog:report"),
            {
                "action": AuditLogEntry.Action.CREATE,
                "model": "invoicing.invoice",
            },
        )

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, "Värde")
        self.assertNotContains(report_response, "<th>Före</th>", html=False)
        self.assertContains(report_response, "Kund AB")

    def test_report_resolves_legacy_foreign_key_ids_to_names(self):
        invoice = Invoice.objects.create(
            company=self.company,
            customer=self.customer,
            invoice_date="2026-07-05",
            due_date="2026-08-04",
            payment_terms_days=30,
        )
        line = invoice.lines.create(
            article=self.article,
            description="Rad med artikel",
            quantity=Decimal("1.00"),
            unit="st",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        AuditLogEntry.objects.create(
            company=self.company,
            company_name=self.company.name,
            action=AuditLogEntry.Action.CREATE,
            actor=self.user,
            actor_display="Ada Audit",
            model_label="invoicing.invoice",
            model_name="Kundfaktura",
            object_pk=str(invoice.pk),
            object_repr=str(invoice),
            summary="Skapad: Kundfaktura",
            changes={"customer": {"before": None, "after": self.customer.pk}},
            metadata={"field_labels": {"customer": "Kund"}},
        )
        AuditLogEntry.objects.create(
            company=self.company,
            company_name=self.company.name,
            action=AuditLogEntry.Action.CREATE,
            actor=self.user,
            actor_display="Ada Audit",
            model_label="invoicing.invoiceline",
            model_name="Fakturarad",
            object_pk=str(line.pk),
            object_repr=str(line),
            summary="Skapad: Fakturarad",
            changes={"article": {"before": None, "after": self.article.pk}},
            metadata={"field_labels": {"article": "Artikel"}},
        )

        invoice_response = self.client.get(
            reverse("auditlog:report"),
            {"action": AuditLogEntry.Action.CREATE, "model": "invoicing.invoice"},
        )
        self.assertContains(invoice_response, "Kund AB")
        self.assertNotContains(invoice_response, ">3<", html=False)

        line_response = self.client.get(
            reverse("auditlog:report"),
            {"action": AuditLogEntry.Action.CREATE, "model": "invoicing.invoiceline"},
        )
        self.assertContains(line_response, "Tjänst")

    def test_report_groups_transaction_with_journal_rows_into_one_item(self):
        response = self.client.post(
            reverse("bookkeeping:transaction_add"),
            {
                "date": "2026-06-30",
                "description": "Samlad verifikation",
                "entries-TOTAL_FORMS": "2",
                "entries-INITIAL_FORMS": "0",
                "entries-MIN_NUM_FORMS": "2",
                "entries-MAX_NUM_FORMS": "1000",
                "entries-0-account": self.cash_account.pk,
                "entries-0-debit": "1250.00",
                "entries-0-credit": "0.00",
                "entries-1-account": self.sales_account.pk,
                "entries-1-debit": "0.00",
                "entries-1-credit": "1250.00",
            },
        )
        self.assertRedirects(response, reverse("bookkeeping:transaction_list"))

        report_response = self.client.get(
            reverse("auditlog:report"),
            {"model": "bookkeeping.transaction"},
        )

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, 'class="accordion-item"', count=1, html=False)
        self.assertContains(report_response, "Relaterade rader")
        self.assertContains(report_response, "Konteringsrad")

    def test_report_groups_invoice_with_invoice_lines_into_one_item(self):
        response = self.client.post(
            reverse("invoicing:invoice_create"),
            {
                "customer": self.customer.pk,
                "ocr_code": "1234567890",
                "invoice_date": "2026-06-26",
                "due_date": "2026-07-26",
                "payment_terms_days": "30",
                "reference": "Ref",
                "notes": "Notering",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-article": self.article.pk,
                "lines-0-description": "Arbete",
                "lines-0-quantity": "2.00",
                "lines-0-unit": "tim",
                "lines-0-unit_price": "1000.00",
                "lines-0-vat_rate": "25.00",
                "lines-0-sort_order": "0",
                "lines-0-line_type": "item",
            },
        )
        self.assertRedirects(response, reverse("invoicing:invoice_list"))

        report_response = self.client.get(
            reverse("auditlog:report"),
            {"model": "invoicing.invoice"},
        )

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, 'class="accordion-item"', count=1, html=False)
        self.assertContains(report_response, "Relaterade rader")
        self.assertContains(report_response, "Fakturarad")

    def test_report_groups_supplier_invoice_with_cost_rows_into_one_item(self):
        invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_number="LEV-2026-2",
            invoice_date="2026-06-24",
            due_date="2026-07-24",
            expense_account=self.expense_account,
            vat_account=self.vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            total_amount=Decimal("500.00"),
            vat_amount=Decimal("100.00"),
            created_by=self.user,
        )
        invoice.cost_lines.create(expense_account=self.expense_account, debit=Decimal("400.00"))

        report_response = self.client.get(
            reverse("auditlog:report"),
            {"model": "supplier_invoices.supplierinvoice"},
        )

        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, 'class="accordion-item"', count=1, html=False)
        self.assertContains(report_response, "Relaterade rader")
        self.assertContains(report_response, "Kostnadsrad leverantörsfaktura")

    def test_report_model_dropdown_shows_only_main_entities(self):
        response = self.client.get(reverse("auditlog:report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kundfaktura")
        self.assertContains(response, "Leverantörsfaktura")
        self.assertContains(response, "Verifikation")
        self.assertNotContains(response, "Fakturarad")
        self.assertNotContains(response, "Konteringsrad")
        self.assertNotContains(response, "Kostnadsrad leverantörsfaktura")


class AuditLogEntryImmutabilityTests(TestCase):
    """DB-level backstop matching AuditLogTests' expectations: nothing may edit or
    delete a logged event after the fact except the hash-chain fields (needed by
    create_audit_log's post-insert stamp and reseal_audit_chain --apply) and
    actor/company (both on_delete=SET_NULL, so deleting a user or company - e.g. via
    bookkeeping/views/companies.py::company_delete - must still be able to null them
    out on every entry that referenced them). See
    auditlog/migrations/0004_lock_audit_log_entries.py.
    """

    def setUp(self):
        self.actor = create_user("logged-actor@example.com")
        self.company = create_company("Oföränderlig logg AB", "556677-5566", users=[self.actor])
        with audit_user(self.actor):
            create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.entry = AuditLogEntry.objects.order_by("-id").first()

    def test_update_of_a_content_field_is_blocked(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AuditLogEntry.objects.filter(pk=self.entry.pk).update(summary="TAMPERED")

    def test_delete_is_blocked(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.entry.delete()

    def test_chain_key_is_frozen(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AuditLogEntry.objects.filter(pk=self.entry.pk).update(chain_key="999")

    def test_hash_fields_remain_updatable(self):
        AuditLogEntry.objects.filter(pk=self.entry.pk).update(entry_hash="0" * 64, prev_hash="1" * 64)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.entry_hash, "0" * 64)
        self.assertEqual(self.entry.prev_hash, "1" * 64)

    def test_deleting_the_company_still_clears_company_via_cascade(self):
        self.assertEqual(self.entry.company_id, self.company.id)

        # Mirrors bookkeeping/views/companies.py::company_delete, which clears
        # PROTECT-guarded relations (here: the auto-created Skattekonto BankAccount,
        # see banking/signals.py) before deleting the company itself.
        self.company.bank_accounts.all().delete()
        self.company.delete()

        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.company_id)
        self.assertTrue(self.entry.company_name)

    def test_deleting_the_actor_still_clears_actor_via_cascade(self):
        self.assertEqual(self.entry.actor_id, self.actor.id)

        self.actor.delete()

        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.actor_id)
        self.assertTrue(self.entry.actor_display)


class AuditChainAnchorTests(TestCase):
    def setUp(self):
        self.company = create_company("Anchor AB", "556677-3344")
        self.account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.account.name = "Företagskonto uppdaterat"
        self.account.save(update_fields=["name"])

    @staticmethod
    def _fake_token_for(message: bytes) -> bytes:
        return b"FAKE-RFC3161-TOKEN:" + message

    def test_anchor_audit_chain_creates_anchor_for_new_tip_and_is_idempotent(self):
        tip = AuditLogEntry.objects.order_by("-id").first()

        with (
            patch("auditlog.management.commands.anchor_audit_chain.request_timestamp") as mock_request,
            patch("auditlog.management.commands.anchor_audit_chain.get_asserted_time") as mock_time,
        ):
            mock_request.return_value = self._fake_token_for(tip.entry_hash.encode("ascii"))
            mock_time.return_value = timezone.now()
            call_command("anchor_audit_chain")

        self.assertEqual(AuditChainAnchor.objects.count(), 1)
        anchor = AuditChainAnchor.objects.get()
        self.assertEqual(anchor.anchored_entry_id, tip.id)
        self.assertEqual(anchor.anchored_entry_hash, tip.entry_hash)
        mock_request.assert_called_once_with(tip.entry_hash.encode("ascii"))

        # Tip hasn't changed since - re-running must not hit the TSA again.
        with patch("auditlog.management.commands.anchor_audit_chain.request_timestamp") as mock_request_again:
            call_command("anchor_audit_chain")
            mock_request_again.assert_not_called()
        self.assertEqual(AuditChainAnchor.objects.count(), 1)

    def test_anchor_audit_chain_anchors_every_company_chain_tip(self):
        other_company = create_company("Anchor Två AB", "556700-7788")
        tips = [
            AuditLogEntry.objects.filter(chain_key=str(company.pk)).order_by("-id").first()
            for company in (self.company, other_company)
        ]

        with (
            patch("auditlog.management.commands.anchor_audit_chain.request_timestamp") as mock_request,
            patch("auditlog.management.commands.anchor_audit_chain.get_asserted_time") as mock_time,
        ):
            mock_request.side_effect = lambda data: self._fake_token_for(data)
            mock_time.return_value = timezone.now()
            call_command("anchor_audit_chain")

        self.assertEqual(AuditChainAnchor.objects.count(), 2)
        self.assertEqual(
            {anchor.anchored_entry_hash for anchor in AuditChainAnchor.objects.all()},
            {tip.entry_hash for tip in tips},
        )

    def test_verify_audit_chain_anchors_detects_tampering_that_survives_a_reseal(self):
        tip = AuditLogEntry.objects.order_by("-id").first()
        with (
            patch("auditlog.management.commands.anchor_audit_chain.request_timestamp") as mock_request,
            patch("auditlog.management.commands.anchor_audit_chain.get_asserted_time") as mock_time,
        ):
            mock_request.return_value = self._fake_token_for(tip.entry_hash.encode("ascii"))
            mock_time.return_value = timezone.now()
            call_command("anchor_audit_chain")

        # Right after anchoring, verification passes (cryptographic check mocked out -
        # the fake token above isn't a real ASN.1 structure, only the hash-match logic
        # is under test here).
        with patch("auditlog.management.commands.verify_audit_chain_anchors.verify_timestamp_token"):
            out = StringIO()
            call_command("verify_audit_chain_anchors", stdout=out)
            self.assertIn("utan avvikelser", out.getvalue())

        # Tamper with the earliest entry, then reseal - reseal recomputes the whole
        # chain, including the tip, so the internal-only check is fooled. Ordinary
        # content tampering (summary, changes, ...) is DB-blocked after creation now
        # (see AuditLogEntryImmutabilityTests) and the hash fields alone can't be used
        # to fake this: entry_hash is a pure function of an entry's (now frozen)
        # content plus its predecessor's hash, so corrupting it directly just gets
        # deterministically restored to the same value by reseal, with no effect on
        # the tip. The only way left to reach this scenario is what it actually takes
        # in production: a DB superuser bypassing the trigger itself (e.g. `ALTER
        # TABLE ... DISABLE TRIGGER` on Postgres). Simulate exactly that, so this test
        # keeps proving the point it was written for - that the external anchor is the
        # backstop even when the DB-level protection is defeated, not merely when it's
        # never been added.
        first_entry = AuditLogEntry.objects.order_by("id").first()
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("DROP TRIGGER trg_auditlog_auditlogentry_no_update ON auditlog_auditlogentry")
            else:
                cursor.execute("DROP TRIGGER trg_auditlog_auditlogentry_no_update")
        AuditLogEntry.objects.filter(pk=first_entry.pk).update(summary="TAMPERED BY ATTACKER")
        call_command("reseal_audit_chain", "--apply")

        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("giltig", out.getvalue())

        # The external anchor still catches it: the tip's entry_hash is no longer
        # what was attested by the TSA before the tampering.
        with patch("auditlog.management.commands.verify_audit_chain_anchors.verify_timestamp_token"):
            with self.assertRaises(SystemExit):
                call_command("verify_audit_chain_anchors")


def _openssl_supports_ts():
    """`openssl ts` is an OpenSSL command LibreSSL does not ship. macOS's
    /usr/bin/openssl is LibreSSL, so verification only works when a real OpenSSL
    (e.g. Homebrew's) comes first on PATH - see the note in TimestampTokenTests.
    """
    openssl = shutil.which("openssl")
    if not openssl:
        return False
    return subprocess.run([openssl, "ts", "-help"], capture_output=True).returncode == 0


def _build_throwaway_tsa(directory: Path, common_name="Test TSA"):
    """Stand up a self-signed CA plus a timeStamping-capable TSA certificate and
    return (ca_cert_path, issue_token) where issue_token(data) -> DER token.

    openssl only embeds the signer certificate when the *request* asks for it
    (-cert), which is what production does via rfc3161ng's
    include_tsa_certificate=True; without it every verification fails with
    "signer certificate not found".
    """

    def run(*args):
        subprocess.run(args, cwd=directory, check=True, capture_output=True)

    run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-days",
        "2",
        "-subj",
        "/CN=Test CA",
    )
    (directory / "ext.cnf").write_text("[v3]\nextendedKeyUsage = critical,timeStamping\n")
    run(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "tsa.key",
        "-out",
        "tsa.csr",
        "-subj",
        f"/CN={common_name}",
    )
    run(
        "openssl",
        "x509",
        "-req",
        "-in",
        "tsa.csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-set_serial",
        "1",
        "-days",
        "2",
        "-extfile",
        "ext.cnf",
        "-extensions",
        "v3",
        "-out",
        "tsa.crt",
    )
    (directory / "tsa.cnf").write_text(
        "[tsa]\ndefault_tsa = tsa_config\n[tsa_config]\n"
        "signer_cert = tsa.crt\ncerts = ca.crt\nsigner_key = tsa.key\n"
        "signer_digest = sha256\ndefault_policy = 1.2.3.4.1\ndigests = sha256\n"
        "accuracy = secs:1\nordering = yes\ntsa_name = yes\n"
        "ess_cert_id_alg = sha256\nserial = serial.txt\n"
    )
    (directory / "serial.txt").write_text("01\n")

    def issue_token(data: bytes) -> bytes:
        (directory / "payload.bin").write_bytes(data)
        run("openssl", "ts", "-query", "-data", "payload.bin", "-sha256", "-no_nonce", "-cert", "-out", "request.tsq")
        run(
            "openssl",
            "ts",
            "-reply",
            "-config",
            "tsa.cnf",
            "-queryfile",
            "request.tsq",
            "-out",
            "token.tsk",
            "-token_out",
        )
        return (directory / "token.tsk").read_bytes()

    return directory / "ca.crt", issue_token


@unittest.skipUnless(_openssl_supports_ts(), "requires an OpenSSL build with the `ts` command")
class TimestampTokenTests(SimpleTestCase):
    """Exercises auditlog/timestamping.py itself.

    AuditChainAnchorTests patches verify_timestamp_token out ("cryptographic check
    mocked out"), so the signature check that the entire external-anchor argument
    rests on was never actually executed by the suite. These tests stand up a
    throwaway TSA and put real, signed RFC 3161 tokens through the production
    verification path.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        ca_cert, issue_token = _build_throwaway_tsa(Path(cls._tmp.name))
        cls.ca_cert = ca_cert
        # staticmethod, or attribute lookup binds it and passes self as `data`.
        cls.issue_token = staticmethod(issue_token)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def test_verify_timestamp_token_accepts_a_genuine_token_for_the_anchored_hash(self):
        anchored = b"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        token = self.issue_token(anchored)

        with override_settings(AUDIT_CHAIN_TSA_CA_CERT=str(self.ca_cert)):
            verify_timestamp_token(token, anchored)  # must not raise

    def test_verify_timestamp_token_rejects_a_token_issued_for_a_different_hash(self):
        """The whole point of anchoring: after a tamper + reseal the entry's hash
        changes, and the old token no longer attests to it."""
        token = self.issue_token(b"hash-before-tampering")

        with override_settings(AUDIT_CHAIN_TSA_CA_CERT=str(self.ca_cert)):
            with self.assertRaises(TimestampVerificationError) as caught:
                verify_timestamp_token(token, b"hash-after-tampering")

        self.assertIn("imprint", str(caught.exception).lower())

    def test_verify_timestamp_token_rejects_a_token_from_an_untrusted_authority(self):
        """Matching the imprint is not enough - the signature has to chain to the
        CA we pinned, or anyone could mint their own 'proof'."""
        with tempfile.TemporaryDirectory() as other_dir:
            _, issue_rogue_token = _build_throwaway_tsa(Path(other_dir), common_name="Rogue TSA")
            anchored = b"hash-anchored-by-a-rogue-tsa"
            rogue_token = issue_rogue_token(anchored)

            with override_settings(AUDIT_CHAIN_TSA_CA_CERT=str(self.ca_cert)):
                with self.assertRaises(TimestampVerificationError):
                    verify_timestamp_token(rogue_token, anchored)

    def test_verify_timestamp_token_reports_a_missing_ca_certificate_by_path(self):
        missing = Path(self._tmp.name) / "nope" / "ca.crt"

        with override_settings(AUDIT_CHAIN_TSA_CA_CERT=str(missing)):
            with self.assertRaises(TimestampVerificationError) as caught:
                verify_timestamp_token(b"irrelevant", b"irrelevant")

        self.assertIn(str(missing), str(caught.exception))

    def test_verify_timestamp_token_rejects_bytes_that_are_not_a_token(self):
        with override_settings(AUDIT_CHAIN_TSA_CA_CERT=str(self.ca_cert)):
            with self.assertRaises(TimestampVerificationError):
                verify_timestamp_token(b"not a DER encoded timestamp at all", b"data")

    def test_get_asserted_time_returns_the_time_the_authority_signed(self):
        before = timezone.now() - timedelta(seconds=30)
        token = self.issue_token(b"hash-for-time-check")

        asserted = get_asserted_time(token)

        self.assertIsNotNone(asserted.tzinfo)
        self.assertGreaterEqual(asserted, before)
        self.assertLessEqual(asserted, timezone.now() + timedelta(seconds=30))


class TimestampRequestTests(SimpleTestCase):
    """request_timestamp talks to a remote TSA, so the network call is mocked;
    what matters here is that a transport failure is translated rather than
    escaping raw - the monthly anchoring job must degrade, not crash."""

    def test_request_timestamp_wraps_transport_failures_and_names_the_authority(self):
        with override_settings(AUDIT_CHAIN_TSA_URL="https://tsa.invalid/tsr"):
            with patch("auditlog.timestamping.rfc3161ng.RemoteTimestamper") as remote:
                remote.return_value.timestamp.side_effect = OSError("connection refused")

                with self.assertRaises(TimestampRequestError) as caught:
                    request_timestamp(b"chain-tip-hash")

        message = str(caught.exception)
        self.assertIn("https://tsa.invalid/tsr", message)
        self.assertIn("connection refused", message)

    def test_request_timestamp_returns_the_token_the_authority_produced(self):
        with patch("auditlog.timestamping.rfc3161ng.RemoteTimestamper") as remote:
            remote.return_value.timestamp.return_value = b"DER-TOKEN"

            self.assertEqual(request_timestamp(b"chain-tip-hash"), b"DER-TOKEN")
            remote.return_value.timestamp.assert_called_once_with(data=b"chain-tip-hash")


class RopaDriftGuardTests(SimpleTestCase):
    """GDPR G-002: varje spårad modell ska finnas i registerförteckningen.

    En ny modell i TRACKED_MODELS utan ROPA-rad är en dokumentationsdrift —
    lägg till modellen i tabellen i docs/compliance/gdpr/ropa.md.
    """

    def test_every_tracked_model_appears_in_ropa(self):
        from django.conf import settings

        from .services import TRACKED_MODELS

        ropa_text = (Path(settings.BASE_DIR) / "docs/compliance/gdpr/ropa.md").read_text()
        missing = [label for label in TRACKED_MODELS if label not in ropa_text]
        self.assertEqual(missing, [])


class SensitiveFieldsGuardTests(SimpleTestCase):
    """GDPR G-007: högriskfält får aldrig in i hashkedjan oredigerade.

    Redigeringen sker före hashning och är bara framåtriktad — ett fält som
    väl hamnat i kedjan kan inte tas bort. Nya spårade modeller med
    personnummer-/lösenords-/hemlighetsfält måste in i sensitive_fields
    innan första posten skapas.
    """

    HIGH_RISK_MARKERS = ("personal_identity_number", "password", "secret")

    def test_high_risk_fields_are_redacted_on_every_tracked_model(self):
        from django.apps import apps

        from .services import TRACKED_MODELS

        unredacted = []
        for label, config in TRACKED_MODELS.items():
            model = apps.get_model(label)
            sensitive = config.get("sensitive_fields", set())
            for field in model._meta.concrete_fields:
                if any(marker in field.name for marker in self.HIGH_RISK_MARKERS):
                    if field.name not in sensitive:
                        unredacted.append(f"{label}.{field.name}")
        self.assertEqual(unredacted, [])
