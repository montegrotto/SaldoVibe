"""Notiskällorna, klockans totaler och den dagliga digesten."""

import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from bookkeeping.context_processors import get_topbar_alert_state_for_company
from bookkeeping.models import AccountClass, SentEmail
from bookkeeping.notifications import (
    get_failed_job_state,
    get_overdue_customer_invoice_state,
    get_overdue_supplier_invoice_state,
    get_vat_deadline_state,
)
from invoicing.models import Customer, Invoice, InvoiceLine
from saldovibe.testing import CompanyTestCase, create_accounts, create_user
from supplier_invoices.models import Supplier, SupplierInvoice

TODAY = datetime.date(2026, 8, 25)


class NotificationSourceTestCase(CompanyTestCase):
    def create_customer_invoice(self, *, due_date, is_booked=True, is_paid=False, credit=False):
        customer, _ = Customer.objects.get_or_create(company=self.company, name="Kund AB")
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=due_date - datetime.timedelta(days=30),
            due_date=due_date,
            is_booked=is_booked,
            is_paid=is_paid,
        )
        if credit:
            # is_credit_invoice är en property: negativ totalsumma.
            InvoiceLine.objects.create(
                invoice=invoice,
                description="Kreditrad",
                quantity=Decimal("1.00"),
                unit_price=Decimal("-100.00"),
                vat_rate=Decimal("25.00"),
                sort_order=0,
            )
        return invoice

    def create_supplier_invoice(self, *, due_date, is_paid=False):
        if not hasattr(self, "_supplier_accounts"):
            self._supplier_accounts = create_accounts(
                self.company,
                [
                    ("4010", "Inköp", AccountClass.COST_OF_GOODS),
                    ("2641", "Ingående moms", AccountClass.EQUITY_LIABILITY),
                    ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
                ],
            )
            self.supplier = Supplier.objects.create(company=self.company, name="Leverantören AB")
        return SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier=self.supplier,
            supplier_name=self.supplier.name,
            invoice_date=due_date - datetime.timedelta(days=30),
            due_date=due_date,
            expense_account=self._supplier_accounts["4010"],
            vat_account=self._supplier_accounts["2641"],
            payable_account=self._supplier_accounts["2440"],
            amount_ex_vat=Decimal("400.00"),
            vat_amount=Decimal("100.00"),
            total_amount=Decimal("500.00"),
            is_paid=is_paid,
            created_by=self.user,
        )


class OverdueInvoiceStateTests(NotificationSourceTestCase):
    user_email = "forfallet@example.com"
    company_name = "Förfallobolaget AB"

    def test_only_booked_unpaid_overdue_customer_invoices_count(self):
        overdue = self.create_customer_invoice(due_date=TODAY - datetime.timedelta(days=5))
        self.create_customer_invoice(due_date=TODAY + datetime.timedelta(days=5))
        self.create_customer_invoice(due_date=TODAY - datetime.timedelta(days=5), is_booked=False)
        self.create_customer_invoice(due_date=TODAY - datetime.timedelta(days=5), is_paid=True)
        self.create_customer_invoice(due_date=TODAY - datetime.timedelta(days=5), credit=True)

        lines, count, signature = get_overdue_customer_invoice_state(self.company, TODAY)
        self.assertEqual(count, 1)
        self.assertEqual(signature, str(overdue.pk))
        self.assertIn("Kund AB", lines[0])

    def test_overdue_supplier_invoices(self):
        overdue = self.create_supplier_invoice(due_date=TODAY - datetime.timedelta(days=2))
        self.create_supplier_invoice(due_date=TODAY + datetime.timedelta(days=2))
        self.create_supplier_invoice(due_date=TODAY - datetime.timedelta(days=2), is_paid=True)

        lines, count, signature = get_overdue_supplier_invoice_state(self.company, TODAY)
        self.assertEqual(count, 1)
        self.assertEqual(signature, str(overdue.pk))
        self.assertIn("Leverantören AB", lines[0])


class VatDeadlineStateTests(NotificationSourceTestCase):
    user_email = "moms@example.com"
    company_name = "Momsdeadlinebolaget AB"
    company_fields = {"vat_reporting_period": "monthly", "vat_start_date": datetime.date(2026, 5, 1)}

    def test_no_reporting_period_means_no_deadlines(self):
        self.company.vat_reporting_period = "none"
        _, count, _ = get_vat_deadline_state(self.company, TODAY)
        self.assertEqual(count, 0)

    def test_undeclared_periods_inside_warning_window(self):
        # Stängda perioder: maj (deadline 12 juli, passerad), juni (12 aug, passerad),
        # juli (12 sep, utanför 14-dagarsfönstret från 25 aug).
        lines, count, _ = get_vat_deadline_state(self.company, TODAY)
        self.assertEqual(count, 2)
        self.assertIn("förföll 2026-07-12", lines[0])
        self.assertIn("förföll 2026-08-12", lines[1])

    def test_declared_period_is_excluded(self):
        from vat.models import VatCloseSnapshot

        VatCloseSnapshot.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=datetime.date(2026, 5, 1),
            period_end=datetime.date(2026, 5, 31),
            source_fingerprint="test",
        )
        _, count, _ = get_vat_deadline_state(self.company, TODAY)
        self.assertEqual(count, 1)


class FailedJobStateTests(NotificationSourceTestCase):
    user_email = "jobbfel@example.com"
    company_name = "Jobbfelbolaget AB"

    def test_counts_recent_failures_but_not_digest_or_sent(self):
        failed = SentEmail.objects.create(
            company=self.company,
            purpose=SentEmail.Purpose.INVOICE,
            recipient="kund@example.com",
            subject="Faktura",
            status=SentEmail.Status.FAILED,
            error="boom",
        )
        SentEmail.objects.create(
            company=self.company,
            purpose=SentEmail.Purpose.DIGEST,
            recipient="anvandare@example.com",
            subject="Digest",
            status=SentEmail.Status.FAILED,
            error="boom",
        )
        SentEmail.objects.create(
            company=self.company,
            purpose=SentEmail.Purpose.INVOICE,
            recipient="kund@example.com",
            subject="Faktura",
            status=SentEmail.Status.SENT,
        )

        lines, count, signature = get_failed_job_state(self.company, TODAY)
        self.assertEqual(count, 1)
        self.assertEqual(signature, str(failed.pk))

    def test_fetch_error_adds_item_and_new_error_changes_signature(self):
        self.company.email_fetch_last_error = "Inloggningen misslyckades"
        self.company.email_fetch_last_error_at = timezone.now()
        lines, count, signature_before = get_failed_job_state(self.company, TODAY)
        self.assertEqual(count, 1)
        self.assertIn("E-postimporten", lines[0])

        self.company.email_fetch_last_error_at = timezone.now() + datetime.timedelta(hours=1)
        _, _, signature_after = get_failed_job_state(self.company, TODAY)
        self.assertNotEqual(signature_before, signature_after)


class TopbarAlertStateTests(NotificationSourceTestCase):
    user_email = "klocka@example.com"
    company_name = "Klockbolaget AB"

    def test_new_sources_feed_count_and_signature(self):
        self.create_customer_invoice(due_date=timezone.localdate() - datetime.timedelta(days=3))
        state = get_topbar_alert_state_for_company(self.company)
        self.assertEqual(state["overdue_customer_invoices_count"], 1)
        self.assertEqual(state["topbar_alert_count"], 1)
        self.assertIn("overduecust:", state["topbar_alert_signature"])

    def test_bell_renders_new_source(self):
        self.create_customer_invoice(due_date=timezone.localdate() - datetime.timedelta(days=3))
        response = self.client.get("/")
        self.assertContains(response, "Förfallna kundfakturor (1)")


class DigestCommandTests(NotificationSourceTestCase):
    user_email = "digest@example.com"
    company_name = "Digestbolaget AB"

    def run_digest(self):
        out = StringIO()
        call_command("skicka_notisdigest", stdout=out)
        return out.getvalue()

    def test_nothing_to_report_sends_nothing(self):
        self.run_digest()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SentEmail.objects.count(), 0)

    def test_sends_one_mail_per_active_user(self):
        self.create_customer_invoice(due_date=timezone.localdate() - datetime.timedelta(days=3))
        colleague = create_user("kollega@example.com")
        self.company.users.add(colleague)
        inactive = create_user("slutat@example.com", is_active=False)
        self.company.users.add(inactive)

        self.run_digest()
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            sorted(message.to[0] for message in mail.outbox),
            ["digest@example.com", "kollega@example.com"],
        )
        self.assertIn("Förfallna kundfakturor", mail.outbox[0].body)
        self.assertIn("Kund AB", mail.outbox[0].body)
        self.assertEqual(
            SentEmail.objects.filter(purpose=SentEmail.Purpose.DIGEST, status=SentEmail.Status.SENT).count(), 2
        )

    def test_send_failure_exits_nonzero(self):
        self.create_customer_invoice(due_date=timezone.localdate() - datetime.timedelta(days=3))
        with (
            patch("bookkeeping.outgoing_mail.django_mail.send_mail", side_effect=OSError("smtp nere")),
            self.assertRaises(SystemExit),
        ):
            call_command("skicka_notisdigest", stdout=StringIO())
        failed = SentEmail.objects.get(purpose=SentEmail.Purpose.DIGEST)
        self.assertEqual(failed.status, SentEmail.Status.FAILED)
