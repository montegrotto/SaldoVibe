import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import ApiToken
from attachments.models import TransactionAttachment
from auditlog.models import AuditLogEntry
from bookkeeping.models import CompanyMembership, JournalEntry, PeriodLock, Transaction
from expenses.models import ExpenseClaim
from invoicing.models import Article, Customer, Invoice, InvoiceLine
from payroll.models import Employee
from saldovibe.testing import DEFAULT_PASSWORD, CompanyTestCase, create_company, create_user
from supplier_invoices.models import Supplier, SupplierInvoice, SupplierInvoiceCostLine

PDF = b"%PDF-1.4 kvitto"


class ApiTestCase(CompanyTestCase):
    """A token-authenticated app client (no session cookie) against a VAT-registered company
    with the full BAS chart. Uploads land in a throwaway MEDIA_ROOT; ReInvGrabber is stubbed."""

    user_email = "app@example.com"
    company_name = "Appbolaget AB"
    company_fields = {"vat_reporting_period": "quarterly"}
    seed_bas_accounts = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media_root = tempfile.mkdtemp(prefix="saldovibe-api-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.client.logout()
        extraction = patch("attachments.services.extract_fields", return_value=None)
        extraction.start()
        self.addCleanup(extraction.stop)
        self.token = ApiToken.issue(self.user, name="Test")
        self.headers = {"Authorization": f"Bearer {self.token}", "X-Company-Id": str(self.company.pk)}

    def account(self, number):
        return self.company.accounts.get(number=number)

    def get(self, path, **params):
        return self.client.get(path, params, headers=self.headers)

    def post(self, path, data=None, **headers):
        return self.client.post(
            path, data=data or {}, content_type="application/json", headers={**self.headers, **headers}
        )

    def pay(self, path, amount):
        return self.post(
            path, {"payment_date": "2026-09-20", "amount": amount, "payment_account": self.account("1930").pk}
        )

    def create_attachment(self, company=None):
        return TransactionAttachment.objects.create(
            company=company or self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto.pdf", PDF, content_type="application/pdf"),
        )


class LoginTests(ApiTestCase):
    def _login(self, password):
        return self.client.post(
            "/api/v1/auth/login/", {"email": self.user_email, "password": password}, content_type="application/json"
        )

    def test_login_returns_a_working_token_and_the_users_companies(self):
        response = self._login(DEFAULT_PASSWORD)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["user"]["email"], self.user_email)
        self.assertEqual(
            data["companies"],
            [{"id": self.company.pk, "name": "Appbolaget AB", "vat_registered": True, "read_only": False}],
        )
        me = self.client.get("/api/v1/me/", headers={"Authorization": f"Bearer {data['token']}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], self.user_email)

    def test_wrong_password_and_inactive_user_are_rejected(self):
        response = self._login("fel-lösenord")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Fel e-postadress eller lösenord."})

        self.user.is_active = False
        self.user.save()
        self.assertEqual(self._login(DEFAULT_PASSWORD).status_code, 400)
        self.assertEqual(ApiToken.objects.filter(user=self.user).count(), 1)  # only setUp's

    def test_repeated_wrong_passwords_lock_the_address(self):
        self.addCleanup(cache.clear)
        for _ in range(10):
            self.assertEqual(self._login("fel-lösenord").status_code, 400)
        self.assertEqual(self._login(DEFAULT_PASSWORD).status_code, 400)

        cache.clear()  # the lockout has expired
        self.assertEqual(self._login(DEFAULT_PASSWORD).status_code, 200)


class TokenTests(TestCase):
    def setUp(self):
        self.user = create_user("token@example.com")
        self.raw = ApiToken.issue(self.user, name="iPhone")
        self.row = ApiToken.objects.get(user=self.user)

    def _age(self, **delta):
        ApiToken.objects.filter(pk=self.row.pk).update(created_at=timezone.now() - timedelta(**delta))

    def test_unknown_or_empty_token_resolves_to_none(self):
        self.assertIsNone(ApiToken.resolve(""))
        self.assertIsNone(ApiToken.resolve("inte-en-token"))

    def test_first_use_stamps_last_used_at(self):
        self.assertIsNone(self.row.last_used_at)
        token = ApiToken.resolve(self.raw)
        self.assertEqual(token.pk, self.row.pk)
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.last_used_at)

    def test_never_used_token_expires_after_fifteen_minutes_and_is_deleted(self):
        self._age(minutes=16)
        self.assertIsNone(ApiToken.resolve(self.raw))
        self.assertFalse(ApiToken.objects.filter(pk=self.row.pk).exists())

    def test_used_token_outlives_the_unused_lifetime(self):
        ApiToken.resolve(self.raw)
        self._age(days=30)
        self.assertIsNotNone(ApiToken.resolve(self.raw))

    def test_token_of_deactivated_user_is_rejected(self):
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(ApiToken.resolve(self.raw))

    def test_logout_deletes_the_token(self):
        headers = {"Authorization": f"Bearer {self.raw}"}
        response = self.client.post("/api/v1/auth/logout/", headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ApiToken.objects.filter(pk=self.row.pk).exists())
        self.assertEqual(self.client.get("/api/v1/me/", headers=headers).status_code, 401)


class CompanyScopeTests(ApiTestCase):
    def test_missing_company_header_is_400(self):
        response = self.client.get("/api/v1/oversikt/", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("X-Company-Id", response.json()["error"])

    def test_company_the_user_is_not_a_member_of_is_403(self):
        other = create_company("Annat AB")
        response = self.client.get("/api/v1/oversikt/", headers={**self.headers, "X-Company-Id": str(other.pk)})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Du har inte tillgång till företaget."})

    def test_superuser_can_use_any_active_company(self):
        admin_token = ApiToken.issue(create_user("admin@example.com", is_superuser=True, is_staff=True))
        response = self.client.get(
            "/api/v1/oversikt/", headers={**self.headers, "Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["company"]["id"], self.company.pk)

    def test_viewer_can_read_but_not_write(self):
        CompanyMembership.objects.filter(company=self.company, user=self.user).update(
            role=CompanyMembership.Role.VIEWER
        )
        self.assertEqual(self.get("/api/v1/utlagg/").status_code, 200)
        self.assertTrue(self.client.get("/api/v1/me/", headers=self.headers).json()["companies"][0]["read_only"])

        response = self.post("/api/v1/utlagg/", {"description": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Du har bara läsbehörighet i det här företaget."})
        self.assertFalse(ExpenseClaim.objects.exists())


class OverviewTests(ApiTestCase):
    def test_overview_reports_open_payables_and_the_overdue_alert(self):
        supplier = Supplier.objects.create(company=self.company, name="Telia")
        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=supplier,
            supplier_name="Telia",
            invoice_date=date(2026, 8, 1),
            due_date=date(2026, 9, 1),
            total_amount=Decimal("1250.00"),
            vat_amount=Decimal("250.00"),
            amount_ex_vat=Decimal("1000.00"),
            payable_account=self.account("2440"),
            vat_account=self.account("2640"),
        )

        response = self.get("/api/v1/oversikt/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(
            {"company", "accounting_year", "revenue", "costs", "net_result", "cash_balance", "bank_accounts"}
            | {"payables_open", "receivables_open", "alerts"},
            set(data),
        )
        self.assertEqual(data["accounting_year"], {"start_date": "2026-01-01", "end_date": "2026-12-31"})
        self.assertEqual(data["payables_open"], {"count": 1, "total": "1250.00"})
        self.assertEqual(data["receivables_open"], {"count": 0, "total": "0.00"})
        overdue = [a for a in data["alerts"] if a["key"] == "overdue_supplier_invoices_count"]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["count"], 1)
        self.assertEqual(overdue[0]["target"], "supplier_invoices")


class FormChoicesTests(ApiTestCase):
    def test_form_choices_lists_accounts_payment_accounts_suppliers_and_employees(self):
        supplier = Supplier.objects.create(company=self.company, name="Telia")
        employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
        )

        response = self.get("/api/v1/formulardata/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["vat_registered"])
        self.assertIn("1930", {a["number"] for a in data["accounts"]})
        self.assertEqual(len(data["accounts"]), self.company.accounts.filter(is_active=True).count())
        self.assertIn(self.account("1930").pk, data["payment_account_ids"])
        self.assertIn(self.account("2893").pk, data["payment_account_ids"])
        self.assertNotIn(self.account("1510").pk, data["payment_account_ids"])
        self.assertEqual(data["default_payment_account_id"], self.account("1930").pk)
        self.assertEqual(data["suppliers"], [{"id": supplier.pk, "name": "Telia"}])
        self.assertEqual(data["employees"], [{"id": employee.pk, "name": "Anna Andersson"}])
        self.assertEqual(data["mileage_rate_per_mil"], "25.00")


class AttachmentTests(ApiTestCase):
    def _upload(self, name="kvitto.pdf", content=PDF, content_type="application/pdf"):
        file = SimpleUploadedFile(name, content, content_type=content_type)
        return self.client.post("/api/v1/bilagor/", {"file": file}, headers=self.headers)

    def test_upload_stores_the_file_for_the_company_as_the_token_user(self):
        response = self._upload()

        self.assertEqual(response.status_code, 201)
        data = response.json()
        attachment = TransactionAttachment.objects.get(pk=data["id"])
        self.assertEqual(data["file_name"], attachment.file_name)
        self.assertIsNone(data["suggestion"])
        self.assertEqual(attachment.company, self.company)
        self.assertEqual(attachment.uploaded_by, self.user)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                company=self.company, actor=self.user, action=AuditLogEntry.Action.CREATE
            ).exists()
        )

    def test_wrong_file_type_is_rejected(self):
        response = self._upload(name="notes.txt", content=b"hej", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Endast PDF, PNG eller JPEG", response.json()["error"])
        self.assertFalse(TransactionAttachment.objects.exists())

    def test_list_shows_the_attachment_until_it_is_linked(self):
        attachment_id = self._upload().json()["id"]
        self.assertEqual([a["id"] for a in self.get("/api/v1/bilagor/").json()], [attachment_id])

        response = self.post(
            "/api/v1/utlagg/",
            {
                "description": "Kvitto",
                "expense_date": "2026-09-15",
                "total_amount": "125.00",
                "vat_amount": "0.00",
                "expense_account": self.account("5410").pk,
                "person_name": "Mattias",
                "attachment_ids": [attachment_id],
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.get("/api/v1/bilagor/").json(), [])

    def test_file_download_is_scoped_to_the_company(self):
        attachment = self.create_attachment()
        response = self.get(f"/api/v1/bilagor/{attachment.pk}/fil/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), PDF)

        foreign = self.create_attachment(company=create_company("Annat AB"))
        response = self.get(f"/api/v1/bilagor/{foreign.pk}/fil/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Hittades inte."})

    def test_file_missing_on_disk_is_404(self):
        attachment = self.create_attachment()
        attachment.file.storage.delete(attachment.file.name)
        response = self.get(f"/api/v1/bilagor/{attachment.pk}/fil/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Hittades inte."})

    def test_swedish_file_name_is_encoded_in_content_disposition(self):
        attachment = TransactionAttachment.objects.create(
            company=self.company,
            uploaded_by=self.user,
            file=SimpleUploadedFile("kvitto_åäö.pdf", PDF, content_type="application/pdf"),
        )
        response = self.get(f"/api/v1/bilagor/{attachment.pk}/fil/")
        self.assertEqual(response["Content-Disposition"], "inline; filename*=utf-8''kvitto_%C3%A5%C3%A4%C3%B6.pdf")


class ExpenseTests(ApiTestCase):
    def _payload(self, **overrides):
        return {
            "description": "Kontorsmaterial",
            "expense_date": "2026-09-15",
            "total_amount": "125.00",
            "vat_amount": "25.00",
            "expense_account": self.account("5410").pk,
            "person_name": "Mattias",
            "register": False,
            **overrides,
        }

    def test_create_draft_links_attachment_and_splits_vat(self):
        attachment = self.create_attachment()
        response = self.post("/api/v1/utlagg/", self._payload(attachment_ids=[attachment.pk]))

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "draft")
        self.assertFalse(data["is_bookkept"])
        self.assertEqual(data["amount_ex_vat"], "100.00")
        self.assertEqual(data["vat_amount"], "25.00")
        self.assertEqual([a["id"] for a in data["attachments"]], [attachment.pk])
        claim = ExpenseClaim.objects.get(pk=data["id"])
        self.assertEqual(list(claim.attachments.all()), [attachment])
        self.assertEqual(claim.person_name, "Mattias")

    def test_create_with_register_books_a_transaction(self):
        response = self.post("/api/v1/utlagg/", self._payload(register=True))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "bookkept")
        self.assertTrue(response.json()["is_bookkept"])
        claim = ExpenseClaim.objects.get(pk=response.json()["id"])
        self.assertTrue(claim.is_registered)
        self.assertTrue(Transaction.objects.filter(pk=claim.registered_transaction_id).exists())

    def test_validation_error_names_the_field(self):
        response = self.post("/api/v1/utlagg/", self._payload(person_name=""))
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertIn("employee", response.json()["errors"])
        self.assertFalse(ExpenseClaim.objects.exists())

    def test_register_endpoint_books_a_draft(self):
        claim_id = self.post("/api/v1/utlagg/", self._payload()).json()["id"]
        response = self.post(f"/api/v1/utlagg/{claim_id}/bokfor/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_bookkept"])
        self.assertTrue(ExpenseClaim.objects.get(pk=claim_id).is_registered)

    def test_payment_marks_the_claim_paid(self):
        claim_id = self.post("/api/v1/utlagg/", self._payload(register=True)).json()["id"]
        response = self.pay(f"/api/v1/utlagg/{claim_id}/betalning/", "125.00")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_paid"])
        self.assertEqual(response.json()["status"], "paid")
        self.assertTrue(ExpenseClaim.objects.get(pk=claim_id).is_paid)

    def test_locked_period_blocks_registration_and_rolls_back_the_create(self):
        draft_id = self.post("/api/v1/utlagg/", self._payload()).json()["id"]
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            reason="Månadsavstämning",
        )

        response = self.post(f"/api/v1/utlagg/{draft_id}/bokfor/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("låst", response.json()["error"])
        self.assertFalse(ExpenseClaim.objects.get(pk=draft_id).is_registered)

        response = self.post("/api/v1/utlagg/", self._payload(register=True))
        self.assertEqual(response.status_code, 400)
        self.assertIn("låst", response.json()["error"])
        self.assertEqual(ExpenseClaim.objects.count(), 1)
        self.assertFalse(Transaction.objects.exists())


class MileageReportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Andersson",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("40000.00"),
        )

    def _payload(self, **overrides):
        return {
            "employee": self.employee.pk,
            "trip_date": "2026-09-15",
            "route": "Stockholm–Uppsala t/r",
            "purpose": "Kundmöte",
            "distance_km": "142,5",
            "rate_per_mil": "25.00",
            "register": True,
            **overrides,
        }

    def test_submit_books_an_expense_on_7331(self):
        response = self.post("/api/v1/korrapporter/", self._payload())

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "bookkept")
        self.assertEqual(data["total_amount"], "356.25")
        self.assertEqual(data["expense_account"]["number"], "7331")
        self.assertEqual(data["mileage"]["route"], "Stockholm–Uppsala t/r")
        self.assertEqual(data["mileage"]["distance_km"], "142.5")
        claim = ExpenseClaim.objects.get(pk=data["id"])
        self.assertEqual(claim.mileage_report.employee, self.employee)
        self.assertTrue(claim.is_registered)
        self.assertEqual(self.get(f"/api/v1/utlagg/{claim.pk}/").json()["mileage"]["purpose"], "Kundmöte")

    def test_draft_is_not_booked(self):
        data = self.post("/api/v1/korrapporter/", self._payload(register=False)).json()
        self.assertEqual(data["status"], "draft")
        self.assertFalse(Transaction.objects.exists())

    def test_validation_error_names_the_field(self):
        response = self.post("/api/v1/korrapporter/", self._payload(distance_km="0"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("distance_km", response.json()["errors"])
        self.assertFalse(ExpenseClaim.objects.exists())

    def test_trip_outside_accounting_years_is_rejected(self):
        response = self.post("/api/v1/korrapporter/", self._payload(trip_date="2001-01-01"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Inget räkenskapsår matchar resdatumet.")
        self.assertFalse(ExpenseClaim.objects.exists())

    def test_plain_expense_detail_has_no_mileage(self):
        data = self.post("/api/v1/utlagg/", ExpenseTests._payload(self)).json()
        self.assertIsNone(data["mileage"])


class SupplierInvoiceTests(ApiTestCase):
    def _payload(self, **overrides):
        return {
            "new_supplier_name": "Telia",
            "invoice_number": "F-1001",
            "invoice_date": "2026-09-10",
            "due_date": "2026-10-10",
            "total_amount": "1250.00",
            "vat_amount": "250.00",
            "expense_account": self.account("6110").pk,
            "register": True,
            **overrides,
        }

    def test_create_with_new_supplier_books_one_cost_line(self):
        response = self.post("/api/v1/leverantorsfakturor/", self._payload())

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "bookkept")
        self.assertEqual(data["supplier_name"], "Telia")
        self.assertEqual(data["amount_ex_vat"], "1000.00")
        supplier = Supplier.objects.get(company=self.company, name="Telia")
        invoice = SupplierInvoice.objects.get(pk=data["id"])
        self.assertEqual(invoice.supplier, supplier)
        line = SupplierInvoiceCostLine.objects.get(invoice=invoice)
        self.assertEqual(line.expense_account, self.account("6110"))
        self.assertEqual(line.debit, Decimal("1000.00"))
        self.assertTrue(invoice.is_registered)

    def test_new_supplier_name_is_matched_case_insensitively(self):
        first = self.post("/api/v1/leverantorsfakturor/", self._payload()).json()
        second = self.post(
            "/api/v1/leverantorsfakturor/", self._payload(new_supplier_name="telia", invoice_number="F-1002")
        ).json()
        self.assertEqual(Supplier.objects.filter(company=self.company).count(), 1)
        self.assertEqual(first["supplier_id"], second["supplier_id"])

    def test_invoice_number_is_unique_per_supplier(self):
        self.assertEqual(self.post("/api/v1/leverantorsfakturor/", self._payload()).status_code, 201)

        duplicate = self.post("/api/v1/leverantorsfakturor/", self._payload())
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            duplicate.json()["errors"]["invoice_number"],
            ["Leverantören har redan en faktura med det här fakturanumret."],
        )

        other_supplier = self.post("/api/v1/leverantorsfakturor/", self._payload(new_supplier_name="Tele2"))
        self.assertEqual(other_supplier.status_code, 201)

    def test_missing_expense_account_is_rejected(self):
        response = self.post("/api/v1/leverantorsfakturor/", self._payload(expense_account=None))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Välj ett kostnadskonto.")
        self.assertFalse(SupplierInvoice.objects.exists())

    def test_payment_marks_the_invoice_paid_and_hides_it_from_the_list(self):
        invoice_id = self.post("/api/v1/leverantorsfakturor/", self._payload()).json()["id"]
        self.assertEqual([i["id"] for i in self.get("/api/v1/leverantorsfakturor/").json()], [invoice_id])

        response = self.pay(f"/api/v1/leverantorsfakturor/{invoice_id}/betalning/", "1250.00")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_paid"])
        self.assertTrue(SupplierInvoice.objects.get(pk=invoice_id).is_paid)

        self.assertEqual(self.get("/api/v1/leverantorsfakturor/").json(), [])
        everything = self.get("/api/v1/leverantorsfakturor/", visa="alla").json()
        self.assertEqual([(i["id"], i["is_paid"]) for i in everything], [(invoice_id, True)])

    def test_draft_can_be_deleted_but_not_a_booked_invoice(self):
        draft_id = self.post("/api/v1/leverantorsfakturor/", self._payload(register=False)).json()["id"]
        booked_id = self.post("/api/v1/leverantorsfakturor/", self._payload(invoice_number="F-1002")).json()["id"]

        response = self.client.delete(f"/api/v1/leverantorsfakturor/{booked_id}/", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Bokförda fakturor kan inte tas bort.")

        response = self.client.delete(f"/api/v1/leverantorsfakturor/{draft_id}/", headers=self.headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(SupplierInvoice.objects.values_list("pk", flat=True)), [booked_id])


class CustomerInvoiceTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        customer = Customer.objects.create(company=self.company, name="Kundbolaget AB")
        article = Article.objects.create(
            company=self.company,
            name="Konsulttimme",
            unit_price=Decimal("1000.00"),
            income_account=self.account("3001"),
        )
        self.invoice = Invoice.objects.create(
            company=self.company, customer=customer, invoice_date=date(2026, 9, 1), due_date=date(2026, 9, 30)
        )
        InvoiceLine.objects.create(
            invoice=self.invoice, article=article, description="Konsultarbete", unit_price=Decimal("1000.00")
        )
        self.invoice.bookkeep(self.user)

    def test_list_and_detail_describe_the_booked_invoice(self):
        response = self.get("/api/v1/kundfakturor/")
        self.assertEqual(response.status_code, 200)
        (row,) = response.json()
        self.assertEqual(row["id"], self.invoice.pk)
        self.assertEqual(row["customer_name"], "Kundbolaget AB")
        self.assertEqual(row["total_amount"], "1250.00")
        self.assertTrue(row["is_overdue"])
        self.assertEqual(row["status"], "bookkept")

        detail = self.get(f"/api/v1/kundfakturor/{self.invoice.pk}/").json()
        self.assertEqual(len(detail["lines"]), 1)
        self.assertEqual(detail["lines"][0]["description"], "Konsultarbete")
        self.assertEqual(detail["lines"][0]["total_ex_vat"], "1000.00")

    def test_payment_marks_the_invoice_paid(self):
        response = self.pay(f"/api/v1/kundfakturor/{self.invoice.pk}/betalning/", "1250.00")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_paid"])
        self.assertEqual(response.json()["status"], "paid")
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.is_paid)
        self.assertEqual(self.get("/api/v1/kundfakturor/").json(), [])

    def test_drafts_and_credit_invoices_are_never_overdue(self):
        credit = Invoice.objects.create(
            company=self.company,
            customer=self.invoice.customer,
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
        )
        InvoiceLine.objects.create(invoice=credit, description="Kreditering", unit_price=Decimal("-1000.00"))
        self.assertTrue(credit.is_credit_invoice)
        self.assertFalse(self.get(f"/api/v1/kundfakturor/{credit.pk}/").json()["is_overdue"])  # draft and credit
        credit.is_booked = True
        credit.save(update_fields=["is_booked"])
        self.assertFalse(self.get(f"/api/v1/kundfakturor/{credit.pk}/").json()["is_overdue"])

    def test_draft_can_be_deleted_but_not_a_booked_invoice(self):
        draft = Invoice.objects.create(
            company=self.company,
            customer=self.invoice.customer,
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
        )
        response = self.client.delete(f"/api/v1/kundfakturor/{self.invoice.pk}/", headers=self.headers)
        self.assertEqual(response.status_code, 400)

        response = self.client.delete(f"/api/v1/kundfakturor/{draft.pk}/", headers=self.headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(Invoice.objects.values_list("pk", flat=True)), [self.invoice.pk])


class ReportTests(ApiTestCase):
    """One sale in March: 1930 D 1250 / 3001 K 1000 / 2611 K 250."""

    def setUp(self):
        super().setUp()
        voucher = Transaction.objects.create(
            accounting_year=self.year, date=date(2026, 3, 15), description="Konsultarvode", created_by=self.user
        )
        for number, debit, credit in (
            ("1930", "1250.00", "0.00"),
            ("3001", "0.00", "1000.00"),
            ("2611", "0.00", "250.00"),
        ):
            JournalEntry.objects.create(
                transaction=voucher, account=self.account(number), debit=Decimal(debit), credit=Decimal(credit)
            )

    @staticmethod
    def section(data, label):
        return next(s for s in data["sections"] if label in (s["title"], s["total_label"]))

    @staticmethod
    def rows(section):
        return [(row["account"]["number"], row["amount"]) for row in section["rows"]]

    def test_income_statement_mirrors_the_web_report_and_takes_the_period_filter(self):
        response = self.get("/api/v1/resultatrakning/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["selected_year"]["id"], self.year.pk)
        self.assertEqual((data["from_month"], data["to_month"]), ("2026-01", "2026-12"))
        self.assertEqual(data["month_choices"][0], {"value": "2026-01", "label": "Jan 2026"})
        revenue = self.section(data, "Rörelsens intäkter")
        self.assertEqual(self.rows(revenue), [("3001", "1000.00")])
        self.assertEqual(revenue["total"], "1000.00")
        self.assertEqual(self.section(data, "Rörelseresultat")["total"], "1000.00")
        self.assertEqual(data["result"], {"label": "Årets resultat", "amount": "1000.00", "note": None})
        # Empty sections are left out, as on the web - except the ones the web always shows.
        self.assertEqual(
            [s["title"] for s in data["sections"]],
            ["Rörelsens intäkter", "Personalkostnader", None, "Finansiella poster"],
        )

        filtered = self.get("/api/v1/resultatrakning/", from_month="2026-04", to_month="2026-06").json()
        self.assertEqual((filtered["period_start"], filtered["period_end"]), ("2026-04-01", "2026-06-30"))
        self.assertEqual(self.rows(self.section(filtered, "Rörelsens intäkter")), [])
        self.assertEqual(filtered["result"]["amount"], "0.00")

    def test_balance_sheet_mirrors_the_web_report(self):
        response = self.get("/api/v1/balansrakning/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([s["title"] for s in data["sections"]], ["Tillgångar", "Kortfristiga skulder", None])
        assets = self.section(data, "Tillgångar")
        self.assertEqual(self.rows(assets), [("1930", "1250.00")])
        self.assertEqual(assets["total"], "1250.00")
        liabilities = self.section(data, "Kortfristiga skulder")
        self.assertEqual(self.rows(liabilities), [("2611", "-250.00")])
        self.assertEqual(liabilities["total"], "250.00")
        self.assertEqual(self.section(data, "Summa eget kapital och skulder")["total"], "250.00")
        self.assertEqual(data["result"], {"label": "Beräknat resultat", "amount": "1000.00", "note": None})

    def test_non_numeric_year_falls_back_to_the_current_year(self):
        for path in ("/api/v1/resultatrakning/", "/api/v1/balansrakning/"):
            self.assertEqual(self.get(path, year="abc").json(), self.get(path).json())
