import hashlib
import io
import json
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from bookkeeping.models import Transaction
from saldovibe.testing import CompanyTestCase, create_accounts, create_user

from .models import (
    Employee,
    EmployeeDefaultAdjustment,
    PayrollReportEvidence,
    PayrollRun,
    SalaryAdjustment,
    SalaryPaymentReminder,
    SalaryRecord,
)


class PayrollFlowTests(CompanyTestCase):
    user_email = "payroll@example.com"
    user_fields = {"is_staff": True}
    company_name = "Lonebolaget AB"
    company_org_number = "556677-8899"

    def setUp(self):
        super().setUp()
        # The payroll code reads this one by its own name.
        self.accounting_year = self.year

        create_accounts(
            self.company,
            [
                ("7010", "Löner till kollektivanställda", "7"),
                ("7510", "Arbetsgivaravgifter", "7"),
                ("2710", "Personalskatt", "2"),
                ("2731", "Avräkning sociala avgifter", "2"),
                ("2910", "Upplupna löner", "2"),
                ("7331", "Skattefria ersättningar", "7"),
                ("7388", "Övriga personalkostnader", "7"),
            ],
        )

        self.employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
            employment_rate=Decimal("100.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

    def test_create_payroll_run_generates_salary_record(self):
        EmployeeDefaultAdjustment.objects.create(
            employee=self.employee,
            phase="pre_tax",
            direction="addition",
            description="Skattepliktig förmån",
            amount=Decimal("1200.00"),
            is_taxable=True,
        )

        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("9500.00"), "reference": "api-ref-1"},
        ):
            response = self.client.post(
                reverse("payroll:payroll_run_create"),
                {
                    "period": "2026-06",
                    "payment_date": "2026-06-25",
                    "generate_salary_records": "on",
                },
            )

        payroll_run = PayrollRun.objects.get(company=self.company, period_year=2026, period_month=6)
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

        record = SalaryRecord.objects.get(payroll_run=payroll_run, employee=self.employee)
        self.assertEqual(record.gross_salary, Decimal("40000.00"))
        self.assertEqual(record.adjustments.count(), 1)
        self.assertTrue(record.adjustments.filter(description="Skattepliktig förmån").exists())
        self.assertEqual(record.preliminary_tax_amount, Decimal("9500.00"))
        self.assertEqual(record.tax_calculation_source, SalaryRecord.TaxCalculationSource.API)
        self.assertEqual(record.tax_calculation_reference, "api-ref-1")
        self.assertEqual(record.employer_contribution_amount, Decimal("12945.00"))
        self.assertEqual(record.net_salary, Decimal("31700.00"))

    def test_salary_report_print_renders_pdf(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=6,
            payment_date=timezone.now().date(),
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.get(reverse("payroll:salary_report_print", args=[payroll_run.pk, record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        from pypdf import PdfReader

        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
        self.assertIn("LÖNESPECIFIKATION", text)
        self.assertIn("Anna Andersson", text)
        self.assertIn("199001011234", text)
        self.assertIn("Nettolön", text)

    def test_mark_skatteverket_reported(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date="2026-07-25",
            created_by=self.user,
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("4200.00"), "reference": "api-ref-2"},
        ):
            SalaryRecord.objects.create(
                payroll_run=payroll_run,
                employee=self.employee,
                gross_salary=Decimal("20000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )

        finish_response = self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.assertEqual(finish_response.status_code, 302)
        self.assertEqual(finish_response.url, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

        mark_response = self.client.post(reverse("payroll:skatteverket_report_mark_reported", args=[payroll_run.pk]))
        self.assertEqual(mark_response.status_code, 302)
        self.assertEqual(mark_response.url, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        payroll_run.refresh_from_db()
        self.assertTrue(payroll_run.is_reported_to_skatteverket)
        evidence = PayrollReportEvidence.objects.get(payroll_run=payroll_run)
        self.assertEqual(len(evidence.payload_hash), 64)

    def test_mark_reported_creates_single_evidence_snapshot(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=12,
            payment_date="2026-12-25",
            created_by=self.user,
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("4300.00"), "reference": "api-ref-snapshot"},
        ):
            SalaryRecord.objects.create(
                payroll_run=payroll_run,
                employee=self.employee,
                gross_salary=Decimal("20000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )

        self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        first_mark = self.client.post(reverse("payroll:skatteverket_report_mark_reported", args=[payroll_run.pk]))
        self.assertEqual(first_mark.status_code, 302)
        self.assertEqual(first_mark.url, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

        evidence = PayrollReportEvidence.objects.get(payroll_run=payroll_run)
        first_hash = evidence.payload_hash

        second_mark = self.client.post(reverse("payroll:skatteverket_report_mark_reported", args=[payroll_run.pk]))
        self.assertEqual(second_mark.status_code, 302)
        self.assertEqual(second_mark.url, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

        payroll_run.refresh_from_db()
        evidence.refresh_from_db()
        self.assertTrue(payroll_run.is_reported_to_skatteverket)
        self.assertEqual(PayrollReportEvidence.objects.filter(payroll_run=payroll_run).count(), 1)
        self.assertEqual(evidence.payload_hash, first_hash)

    def test_reported_run_can_download_evidence_package(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=5,
            payment_date="2026-05-25",
            created_by=self.user,
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("4100.00"), "reference": "api-ref-evidence"},
        ):
            SalaryRecord.objects.create(
                payroll_run=payroll_run,
                employee=self.employee,
                gross_salary=Decimal("20000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )

        self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.client.post(reverse("payroll:skatteverket_report_mark_reported", args=[payroll_run.pk]))

        response = self.client.get(reverse("payroll:skatteverket_report_evidence_download", args=[payroll_run.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response["Content-Type"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertEqual(sorted(archive.namelist()), ["agi_payload.json", "manifest.json"])
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        payload_raw = archive.read("agi_payload.json")
        payload_hash = hashlib.sha256(payload_raw).hexdigest()
        self.assertEqual(manifest["payload_sha256"], payload_hash)

    def test_agi_xml_download_produces_valid_structure(self):
        import xml.etree.ElementTree as ET

        self.company.phone_number = "08-1234567"
        self.company.save(update_fields=["phone_number"])

        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=6,
            payment_date="2026-06-25",
            created_by=self.user,
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("9500.00"), "reference": "api-ref-agi"},
        ):
            SalaryRecord.objects.create(
                payroll_run=payroll_run,
                employee=self.employee,
                gross_salary=Decimal("40000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )
            # mark_payroll_run_finished recalculates each record right before posting,
            # so the mock must still be active for that call too.
            self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))

        response = self.client.get(reverse("payroll:skatteverket_agi_xml_download", args=[payroll_run.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])

        ns = {"agd": "http://xmls.skatteverket.se/se/skatteverket/da/komponent/schema/1.1"}
        root = ET.fromstring(response.content)
        self.assertEqual(root.attrib["omrade"], "Arbetsgivardeklaration")

        hu = root.find(".//agd:Blankett/agd:Blankettinnehall/agd:HU", ns)
        self.assertIsNotNone(hu)
        self.assertEqual(hu.find("agd:ArbetsgivareHUGROUP/agd:AgRegistreradId", ns).attrib["faltkod"], "201")
        # Org number 556677-8899 -> IDENTITET with the "16" org-number century prefix.
        self.assertEqual(hu.find("agd:ArbetsgivareHUGROUP/agd:AgRegistreradId", ns).text, "165566778899")
        self.assertEqual(hu.find("agd:RedovisningsPeriod", ns).text, "202606")
        self.assertEqual(hu.find("agd:SummaSkatteavdr", ns).text, "9500")

        iu = root.find(".//agd:Blankett/agd:Blankettinnehall/agd:IU", ns)
        self.assertIsNotNone(iu)
        payee_id = iu.find(
            "agd:BetalningsmottagareIUGROUP/agd:BetalningsmottagareIDChoice/agd:BetalningsmottagarId", ns
        )
        self.assertEqual(payee_id.text, "199001011234")
        self.assertEqual(iu.find("agd:AvdrPrelSkatt", ns).text, "9500")
        self.assertEqual(iu.find("agd:Specifikationsnummer", ns).text, "001")

    def test_agi_xml_download_blocked_when_company_phone_missing(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date="2026-07-25",
            created_by=self.user,
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("9500.00"), "reference": "api-ref-agi-2"},
        ):
            SalaryRecord.objects.create(
                payroll_run=payroll_run,
                employee=self.employee,
                gross_salary=Decimal("40000.00"),
                tax_table_number=32,
                tax_table_column=1,
            )
        self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))

        response = self.client.get(reverse("payroll:skatteverket_agi_xml_download", args=[payroll_run.pk]))

        self.assertEqual(response.status_code, 302)
        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("telefonnummer" in text for text in message_texts), message_texts)

    def test_non_finance_admin_cannot_mark_reported(self):
        non_staff = create_user("payroll-nonstaff@example.com", is_staff=False)
        self.company.users.add(non_staff)

        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=4,
            payment_date="2026-04-25",
            created_by=self.user,
            is_finished=True,
        )

        self.client.force_login(non_staff)
        response = self.client.post(reverse("payroll:skatteverket_report_mark_reported", args=[payroll_run.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))
        payroll_run.refresh_from_db()
        self.assertFalse(payroll_run.is_reported_to_skatteverket)

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("8200.00"), "reference": "api-ref-finish"},
    )
    def test_finish_creates_booking_transaction_and_salary_payment_reminders(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date="2026-07-25",
            created_by=self.user,
        )
        SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

        payroll_run.refresh_from_db()
        self.assertTrue(payroll_run.is_finished)
        self.assertIsNotNone(payroll_run.booking_transaction_id)

        txn = Transaction.objects.get(pk=payroll_run.booking_transaction_id)
        self.assertEqual(txn.entries.count(), 5)
        self.assertTrue(txn.is_balanced)

        reminder = SalaryPaymentReminder.objects.get(payroll_run=payroll_run, employee=self.employee)
        self.assertEqual(reminder.amount, Decimal("31800.00"))
        self.assertEqual(reminder.due_date.isoformat(), "2026-07-25")

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("9000.00"), "reference": "api-ref-balance"},
    )
    def test_finish_booking_is_balanced_with_adjustments(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=8,
            payment_date="2026-08-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
            non_taxable_additions=Decimal("500.00"),
        )
        SalaryAdjustment.objects.create(
            salary_record=record,
            phase="post_tax",
            direction="deduction",
            description="Nettolöneavdrag",
            amount=Decimal("200.00"),
            is_taxable=False,
        )

        response = self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        payroll_run.refresh_from_db()

        txn = Transaction.objects.get(pk=payroll_run.booking_transaction_id)
        net_salary_liability = txn.entries.get(account__number="2910")
        deduction_entry = txn.entries.get(account__number="7388", description="Lönejustering avdrag")
        self.assertEqual(net_salary_liability.credit, Decimal("31300.00"))
        self.assertEqual(deduction_entry.credit, Decimal("200.00"))
        self.assertTrue(txn.is_balanced)

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("9000.00"), "reference": "api-ref-totals"},
    )
    def test_payslip_totals_include_line_adjustments(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=8,
            payment_date="2026-08-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
            non_taxable_additions=Decimal("500.00"),
        )
        for phase, direction, amount, taxable in (
            ("pre_tax", "addition", "1000.00", True),
            ("pre_tax", "deduction", "300.00", False),
            ("post_tax", "addition", "100.00", False),
            ("post_tax", "deduction", "200.00", False),
        ):
            SalaryAdjustment.objects.create(
                salary_record=record,
                phase=phase,
                direction=direction,
                description=direction,
                amount=Decimal(amount),
                is_taxable=taxable,
            )

        self.assertEqual(record.total_taxable_additions, Decimal("1000.00"))
        self.assertEqual(record.total_pre_tax_deductions, Decimal("300.00"))
        self.assertEqual(record.total_non_taxable_additions, Decimal("600.00"))
        self.assertEqual(record.total_post_tax_deductions, Decimal("200.00"))

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("9000.00"), "reference": "api-ref-milage"},
    )
    def test_finish_books_skattefri_milersattning_on_7331(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=9,
            payment_date="2026-09-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        SalaryAdjustment.objects.create(
            salary_record=record,
            phase="post_tax",
            direction="addition",
            category="tax_free_mileage",
            description="Skattefri milersättning",
            amount=Decimal("1000.00"),
            is_taxable=False,
        )

        response = self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        payroll_run.refresh_from_db()

        txn = Transaction.objects.get(pk=payroll_run.booking_transaction_id)
        milage_entry = txn.entries.get(account__number="7331", description="Lönejustering tillägg")
        self.assertEqual(milage_entry.debit, Decimal("1000.00"))
        self.assertEqual(milage_entry.credit, Decimal("0.00"))
        self.assertTrue(txn.is_balanced)

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("9000.00"), "reference": "api-ref-milage-edited"},
    )
    def test_finish_books_edited_description_on_category_account(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=10,
            payment_date="2026-10-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        SalaryAdjustment.objects.create(
            salary_record=record,
            phase="post_tax",
            direction="addition",
            category="tax_free_mileage",
            description="Skattefri milersättning – oktober, resor till kund",
            amount=Decimal("1000.00"),
            is_taxable=False,
        )

        response = self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        payroll_run.refresh_from_db()

        txn = Transaction.objects.get(pk=payroll_run.booking_transaction_id)
        milage_entry = txn.entries.get(account__number="7331", description="Lönejustering tillägg")
        self.assertEqual(milage_entry.debit, Decimal("1000.00"))
        self.assertEqual(milage_entry.credit, Decimal("0.00"))
        self.assertTrue(txn.is_balanced)

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("8200.00"), "reference": "api-ref-lock"},
    )
    def test_agi_xml_download_requires_finished_payroll_run(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=7,
            payment_date="2026-07-25",
            created_by=self.user,
        )
        SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.get(reverse("payroll:skatteverket_agi_xml_download", args=[payroll_run.pk]))
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))

    @patch("payroll.models.get_tax_amount_from_skatteverket", return_value=None)
    def test_payroll_run_create_shows_error_when_api_unavailable(self, _api_mock):
        response = self.client.post(
            reverse("payroll:payroll_run_create"),
            {
                "period": "2026-08",
                "payment_date": "2026-08-25",
                "generate_salary_records": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kunde inte beräkna skatt via Skatteverkets API")
        self.assertFalse(PayrollRun.objects.filter(company=self.company, period_year=2026, period_month=8).exists())

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("8000.00"), "reference": "api-ref-3"},
    )
    def test_can_add_and_remove_employee_before_reported(self, _api_mock):
        employee2 = Employee.objects.create(
            company=self.company,
            first_name="Bertil",
            last_name="Bengtsson",
            personal_identity_number="198512129999",
            monthly_salary=Decimal("35000.00"),
            employment_rate=Decimal("100.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=8,
            payment_date="2026-08-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        self.assertEqual(record.tax_calculation_source, SalaryRecord.TaxCalculationSource.API)
        self.assertEqual(record.preliminary_tax_amount, Decimal("8000.00"))

        add_response = self.client.post(
            reverse("payroll:payroll_run_add_employee", args=[payroll_run.pk]),
            {"employee_id": employee2.pk},
        )
        self.assertRedirects(add_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertTrue(SalaryRecord.objects.filter(payroll_run=payroll_run, employee=employee2).exists())

        remove_response = self.client.post(
            reverse("payroll:payroll_run_remove_employee", args=[payroll_run.pk, record.pk]),
        )
        self.assertRedirects(remove_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertFalse(SalaryRecord.objects.filter(pk=record.pk).exists())

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("8000.00"), "reference": "api-ref-lock2"},
    )
    def test_cannot_add_or_remove_employee_after_finished(self, _api_mock):
        employee2 = Employee.objects.create(
            company=self.company,
            first_name="David",
            last_name="Dahl",
            personal_identity_number="198611119999",
            monthly_salary=Decimal("35000.00"),
            employment_rate=Decimal("100.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=9,
            payment_date="2026-09-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        self.client.post(reverse("payroll:payroll_run_finish", args=[payroll_run.pk]))

        add_response = self.client.post(
            reverse("payroll:payroll_run_add_employee", args=[payroll_run.pk]),
            {"employee_id": employee2.pk},
        )
        self.assertRedirects(add_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertFalse(SalaryRecord.objects.filter(payroll_run=payroll_run, employee=employee2).exists())

        remove_response = self.client.post(
            reverse("payroll:payroll_run_remove_employee", args=[payroll_run.pk, record.pk]),
        )
        self.assertRedirects(remove_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertTrue(SalaryRecord.objects.filter(pk=record.pk).exists())

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("7800.00"), "reference": "api-ref-4"},
    )
    def test_cannot_add_or_remove_employee_after_reported(self, _api_mock):
        employee2 = Employee.objects.create(
            company=self.company,
            first_name="Cecilia",
            last_name="Carlsson",
            personal_identity_number="198001019999",
            monthly_salary=Decimal("30000.00"),
            employment_rate=Decimal("100.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=10,
            payment_date="2026-10-25",
            created_by=self.user,
            is_reported_to_skatteverket=True,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        add_response = self.client.post(
            reverse("payroll:payroll_run_add_employee", args=[payroll_run.pk]),
            {"employee_id": employee2.pk},
        )
        self.assertRedirects(add_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertFalse(SalaryRecord.objects.filter(payroll_run=payroll_run, employee=employee2).exists())

        remove_response = self.client.post(
            reverse("payroll:payroll_run_remove_employee", args=[payroll_run.pk, record.pk]),
        )
        self.assertRedirects(remove_response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertTrue(SalaryRecord.objects.filter(pk=record.pk).exists())

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("9150.00"), "reference": "api-ref-5"},
    )
    def test_can_edit_salary_record_with_additions_and_deductions_before_reported(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=11,
            payment_date="2026-11-25",
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.post(
            reverse("payroll:salary_record_update", args=[payroll_run.pk, record.pk]),
            {
                "gross_salary": "40000.00",
                "tax_table_number": "32",
                "tax_table_column": "1",
                "adjustments-TOTAL_FORMS": "3",
                "adjustments-INITIAL_FORMS": "0",
                "adjustments-MIN_NUM_FORMS": "0",
                "adjustments-MAX_NUM_FORMS": "1000",
                "adjustments-0-phase": "pre_tax",
                "adjustments-0-direction": "addition",
                "adjustments-0-description": "OB-tillägg",
                "adjustments-0-amount": "2500.00",
                "adjustments-0-is_taxable": "on",
                "adjustments-1-phase": "pre_tax",
                "adjustments-1-direction": "deduction",
                "adjustments-1-description": "Bruttolöneavdrag",
                "adjustments-1-amount": "1000.00",
                "adjustments-2-phase": "post_tax",
                "adjustments-2-direction": "addition",
                "adjustments-2-description": "Skattefri milersättning",
                "adjustments-2-amount": "1200.00",
            },
        )
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        record.refresh_from_db()
        self.assertEqual(record.adjustments.count(), 3)
        self.assertTrue(record.adjustments.filter(description="OB-tillägg").exists())
        self.assertTrue(record.adjustments.filter(description="Bruttolöneavdrag").exists())
        self.assertTrue(record.adjustments.filter(description="Skattefri milersättning").exists())
        self.assertEqual(record.preliminary_tax_amount, Decimal("9150.00"))
        self.assertEqual(record.net_salary, Decimal("33550.00"))

    @patch(
        "payroll.models.get_tax_amount_from_skatteverket",
        return_value={"tax_amount": Decimal("7900.00"), "reference": "api-ref-6"},
    )
    def test_cannot_edit_salary_record_after_reported(self, _api_mock):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=12,
            payment_date="2026-12-25",
            created_by=self.user,
            is_reported_to_skatteverket=True,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.post(
            reverse("payroll:salary_record_update", args=[payroll_run.pk, record.pk]),
            {
                "gross_salary": "41000.00",
                "tax_table_number": "32",
                "tax_table_column": "1",
                "adjustments-TOTAL_FORMS": "0",
                "adjustments-INITIAL_FORMS": "0",
                "adjustments-MIN_NUM_FORMS": "0",
                "adjustments-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertRedirects(response, reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        record.refresh_from_db()
        self.assertEqual(record.gross_salary, Decimal("40000.00"))

    def test_topbar_alert_shows_for_payroll_run_due_within_three_days(self):
        today = timezone.localdate()

        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=today.year,
            period_month=today.month,
            payment_date=today + timedelta(days=1),
            created_by=self.user,
        )
        SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fixed-assets-alert-menu")
        self.assertContains(response, "payroll-runs-alert-item")

    def test_topbar_alert_hides_payroll_run_once_fully_paid(self):
        today = timezone.localdate()

        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=today.year,
            period_month=today.month,
            payment_date=today + timedelta(days=1),
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        payroll_run.paid_amount = record.net_salary
        payroll_run.save(update_fields=["paid_amount"])

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "payroll-runs-alert-item")


class PersonnummerMaskingTests(CompanyTestCase):
    user_email = "gdpr@example.com"
    user_fields = {"is_staff": True}

    def setUp(self):
        super().setUp()
        self.employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
        )

    def test_masked_property(self):
        self.assertEqual(self.employee.masked_personal_identity_number, "19900101-XXXX")
        self.employee.personal_identity_number = ""
        self.assertEqual(self.employee.masked_personal_identity_number, "XXXX")

    def test_employee_list_shows_only_masked_personnummer(self):
        response = self.client.get(reverse("payroll:employee_list"))
        self.assertContains(response, "19900101-XXXX")
        self.assertNotContains(response, "199001011234")

    def test_payroll_run_detail_shows_only_masked_personnummer(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=1,
            payment_date=timezone.localdate(),
            created_by=self.user,
        )
        response = self.client.get(reverse("payroll:payroll_run_detail", args=[payroll_run.pk]))
        self.assertContains(response, "19900101-XXXX")
        self.assertNotContains(response, "199001011234")

    def test_lonebesked_still_shows_full_personnummer(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=1,
            payment_date=timezone.localdate(),
            created_by=self.user,
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=self.employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )
        from django.template.loader import render_to_string

        from .views import salary_report_pdf_context

        html = render_to_string("payroll/salary_report_print.html", salary_report_pdf_context(record))
        self.assertIn("199001011234", html)


class PersonnummerEncryptionTests(CompanyTestCase):
    user_email = "kryptering@example.com"

    def _create_employee(self, pnr="199001011234"):
        return Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number=pnr,
            monthly_salary=Decimal("40000.00"),
        )

    def test_round_trip_through_db(self):
        employee = self._create_employee()
        employee.refresh_from_db()
        self.assertEqual(employee.personal_identity_number, "199001011234")

    def test_db_value_is_not_plaintext(self):
        from django.db import connection

        from saldovibe.encryption import decrypt_value

        employee = self._create_employee()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT personal_identity_number FROM payroll_employee WHERE id = %s",
                [employee.pk],
            )
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, "199001011234")
        self.assertTrue(raw.startswith("gAAAAA"))
        self.assertEqual(decrypt_value(raw), "199001011234")

    def test_legacy_plaintext_passes_through_decrypt(self):
        from saldovibe.encryption import decrypt_value

        self.assertEqual(decrypt_value("199001011234"), "199001011234")

    def test_duplicate_pnr_in_same_company_blocked(self):
        from django.db import IntegrityError

        self._create_employee()
        with self.assertRaises(IntegrityError):
            self._create_employee()

    def test_blind_index_ignores_dash(self):
        from saldovibe.encryption import blind_index

        self.assertEqual(blind_index("19900101-1234"), blind_index("199001011234"))


class EmployeeFormTests(CompanyTestCase):
    def _form_data(self, pnr):
        from django.http import QueryDict

        data = QueryDict(mutable=True)
        data.update(
            {
                "first_name": "Anna",
                "last_name": "Andersson",
                "personal_identity_number": pnr,
                "monthly_salary": "35000",
                "employment_rate": "100.00",
                "tax_table_number": "34",
                "tax_table_column": "1",
                "is_active": "on",
            }
        )
        return data

    def test_personnummer_with_dash_is_normalized(self):
        from payroll.forms import EmployeeForm

        form = EmployeeForm(data=self._form_data("19900101-1234"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["personal_identity_number"], "199001011234")

    def test_personnummer_requires_twelve_digits(self):
        from payroll.forms import EmployeeForm

        form = EmployeeForm(data=self._form_data("900101-1234"))
        self.assertFalse(form.is_valid())
        self.assertIn("personal_identity_number", form.errors)


class PayrollRegressionTests(CompanyTestCase):
    def setUp(self):
        super().setUp()
        self.employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("1000.00"),
        )
        self.payroll_run = PayrollRun.objects.create(
            company=self.company, period_year=2026, period_month=6, payment_date="2026-06-25", created_by=self.user
        )
        self.company.org_number = "556677-8899"
        self.company.phone_number = "08-1234567"
        self.company.save(update_fields=["org_number", "phone_number"])

    def test_negative_net_salary_is_a_validation_error(self):
        from django.core.exceptions import ValidationError

        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("0.00"), "reference": "x"},
        ):
            with self.assertRaises(ValidationError):
                SalaryRecord.objects.create(
                    payroll_run=self.payroll_run,
                    employee=self.employee,
                    gross_salary=Decimal("1000.00"),
                    post_tax_deductions=Decimal("5000.00"),
                    tax_table_number=32,
                    tax_table_column=1,
                )

    def test_agi_hu_totals_equal_sum_of_rounded_iu_rows(self):
        import xml.etree.ElementTree as ET

        from .agi import generate_agi_xml

        other = Employee.objects.create(
            company=self.company,
            first_name="Bo",
            last_name="Berg",
            personal_identity_number="198501011239",
            monthly_salary=Decimal("1000.00"),
        )
        with patch(
            "payroll.models.get_tax_amount_from_skatteverket",
            return_value={"tax_amount": Decimal("100.50"), "reference": "x"},
        ):
            for employee in (self.employee, other):
                SalaryRecord.objects.create(
                    payroll_run=self.payroll_run,
                    employee=employee,
                    gross_salary=Decimal("1000.00"),
                    tax_table_number=32,
                    tax_table_column=1,
                )
        xml = generate_agi_xml(self.payroll_run, sender_name="A", sender_email="a@example.com", sender_phone="1")
        ns = {"agd": "http://xmls.skatteverket.se/se/skatteverket/da/komponent/schema/1.1"}
        root = ET.fromstring(xml)
        hu_total = int(root.find(".//agd:HU/agd:SummaSkatteavdr", ns).text)
        iu_sum = sum(int(el.text) for el in root.findall(".//agd:IU/agd:AvdrPrelSkatt", ns))
        self.assertEqual(hu_total, iu_sum)

    def test_reported_run_evidence_blocks_deletion(self):
        from django.db.models import ProtectedError

        PayrollReportEvidence.objects.create(payroll_run=self.payroll_run, payload_json="{}", payload_hash="0" * 64)
        with self.assertRaises(ProtectedError):
            self.payroll_run.delete()
