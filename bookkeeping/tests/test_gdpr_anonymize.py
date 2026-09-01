"""GDPR G-006: anonymisering bevarar FK-integritet och auditkedjan."""

import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import CustomUser
from payroll.models import Employee, PayrollRun
from saldovibe.testing import CompanyTestCase


class GdprAnonymizeTests(CompanyTestCase):
    user_email = "erase-me@example.com"
    company_name = "Anonymbolaget AB"

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp()

    def _anonymize(self, **kwargs):
        with override_settings(DATA_DIR=Path(self.tmpdir)):
            call_command("gdpr_anonymize", "--yes", stdout=StringIO(), **kwargs)

    def test_requires_confirmation_flag(self):
        with self.assertRaises(CommandError):
            call_command("gdpr_anonymize", user=self.user_email, stdout=StringIO())

    def test_user_anonymization_blocks_login_and_keeps_fk(self):
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=2026,
            period_month=1,
            payment_date=timezone.localdate(),
            created_by=self.user,
        )

        self._anonymize(user=self.user_email)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, f"raderad-anvandare-{self.user.pk}@example.invalid")
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(CustomUser.objects.filter(email=self.user_email).exists())

        payroll_run.refresh_from_db()
        self.assertEqual(payroll_run.created_by_id, self.user.pk)

    def test_employee_anonymization_keeps_chain_valid(self):
        employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
            address="Storgatan 1",
            postal_code="11122",
            city="Stockholm",
        )

        self._anonymize(employee=employee.pk)

        employee.refresh_from_db()
        self.assertEqual(employee.address, "")
        self.assertEqual(employee.postal_code, "")
        self.assertEqual(employee.city, "")
        self.assertEqual(employee.first_name, "Anna")
        self.assertEqual(employee.personal_identity_number, "199001011234")

        # Anonymiseringen är själv auditloggad och kedjan förblir giltig.
        call_command("verify_audit_chain", stdout=StringIO())

    def test_erasure_log_appended(self):
        self._anonymize(user=self.user_email)
        log_lines = (Path(self.tmpdir) / "gdpr-erasures.jsonl").read_text().splitlines()
        record = json.loads(log_lines[-1])
        self.assertEqual(record["subject"], "accounts.customuser")
        self.assertEqual(record["action"], "user_anonymized")
