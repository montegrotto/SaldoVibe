"""GDPR G-005: registerutdraget ska innehålla alla rader som refererar subjektet."""

import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from payroll.models import Employee, PayrollRun, SalaryRecord
from saldovibe.testing import CompanyTestCase


class DsarExportTests(CompanyTestCase):
    user_email = "dsar@example.com"
    company_name = "DSAR-bolaget AB"

    def _export(self, **kwargs):
        out = StringIO()
        call_command("dsar_export", stdout=out, **kwargs)
        return json.loads(out.getvalue())

    def test_employee_export_contains_salary_rows_and_audit_trail(self):
        employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
        )
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=1,
            payment_date=timezone.localdate(),
        )
        record = SalaryRecord.objects.create(
            payroll_run=payroll_run,
            employee=employee,
            gross_salary=Decimal("40000.00"),
            tax_table_number=32,
            tax_table_column=1,
        )

        result = self._export(employee=employee.pk)

        self.assertEqual(result["subject_model"], "payroll.employee")
        self.assertEqual(result["subject"]["personal_identity_number"], "199001011234")
        self.assertNotIn("personal_identity_number_hash", result["subject"])

        related_models = {group["model"] for group in result["related"]}
        self.assertIn("payroll.salaryrecord", related_models)
        salary_rows = next(group["rows"] for group in result["related"] if group["model"] == "payroll.salaryrecord")
        self.assertEqual(salary_rows[0]["id"], record.pk)

        audit_models = {entry["model"] for entry in result["audit_entries"]}
        self.assertIn("payroll.employee", audit_models)
        self.assertIn("payroll.salaryrecord", audit_models)

    def test_user_export_contains_membership_and_actor_log_without_password(self):
        result = self._export(user=self.user_email)

        self.assertEqual(result["subject_model"], "accounts.customuser")
        self.assertEqual(result["subject"]["email"], self.user_email)
        self.assertNotIn("password", result["subject"])

        related_models = {group["model"] for group in result["related"]}
        self.assertIn("bookkeeping.company", related_models)
        self.assertIn("audit_entries_as_actor", result)

    def test_user_export_excludes_encrypted_company_secrets(self):
        self.company.email_send_smtp_password = "SUPER-SECRET"
        self.company.save()
        result = self._export(user=self.user_email)
        company_rows = next(group["rows"] for group in result["related"] if group["model"] == "bookkeeping.company")
        self.assertNotIn("email_send_smtp_password", company_rows[0])
        self.assertNotIn("SUPER-SECRET", json.dumps(result))
