import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import ApiToken
from attachments.models import TransactionAttachment
from auditlog.models import AuditLogEntry
from bookkeeping.models import CompanyMembership, PeriodLock, Transaction
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
