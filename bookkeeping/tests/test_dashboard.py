from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from bookkeeping.models import AccountClass, Transaction
from saldovibe.testing import CompanyTestCase, create_account, create_accounting_year, create_accounts, create_user


class DashboardLiquidityForecastTests(CompanyTestCase):
    user_email = "dashboard-liquidity@example.com"
    company_name = "Likvid AB"
    company_org_number = "556600-1122"
    # The forecast looks six weeks ahead from today, so the year has to be *this* year.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.year = create_accounting_year(self.company, f"{today.year}-01-01", f"{today.year}-12-31")

        accounts = create_accounts(
            self.company,
            [
                ("1930", "Företagskonto", AccountClass.ASSET),
                ("2010", "Eget kapital", AccountClass.EQUITY_LIABILITY),
                ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
            ],
        )
        self.bank_account = accounts["1930"]
        self.equity_account = accounts["2010"]
        self.payable_account = accounts["2440"]

        opening_txn = Transaction.objects.create(
            accounting_year=self.year,
            date=today,
            description="Ingående saldo",
            created_by=self.user,
        )
        from bookkeeping.models import JournalEntry

        JournalEntry.objects.create(
            transaction=opening_txn, account=self.bank_account, debit=Decimal("100000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=opening_txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("100000.00")
        )

    def test_dashboard_includes_six_week_liquidity_forecast_data(self):
        from invoicing.models import Customer, Invoice, InvoiceLine
        from payroll.models import Employee, PayrollRun, SalaryRecord
        from supplier_invoices.models import SupplierInvoice

        today = timezone.localdate()

        customer = Customer.objects.create(
            company=self.company,
            name="Kund AB",
        )
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=today,
            due_date=today + timedelta(days=10),
            payment_terms_days=10,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Tjänster",
            quantity=Decimal("1.00"),
            unit_price=Decimal("1250.00"),
            vat_rate=Decimal("0.00"),
        )

        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier_name="Leverantör AB",
            invoice_date=today,
            due_date=today + timedelta(days=12),
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("800.00"),
            total_amount=Decimal("800.00"),
            vat_amount=Decimal("0.00"),
            is_paid=False,
        )

        employee = Employee.objects.create(
            company=self.company,
            first_name="Anna",
            last_name="Anställd",
            personal_identity_number="199001011234",
            monthly_salary=Decimal("30000.00"),
        )
        payroll_run = PayrollRun.objects.create(
            company=self.company,
            period_year=today.year,
            period_month=today.month,
            payment_date=today + timedelta(days=15),
            created_by=self.user,
        )
        SalaryRecord.objects.bulk_create(
            [
                SalaryRecord(
                    payroll_run=payroll_run,
                    employee=employee,
                    gross_salary=Decimal("30000.00"),
                    net_salary=Decimal("22000.00"),
                )
            ]
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cash_balance"], Decimal("100000.00"))
        self.assertEqual(response.context["incoming_total"], Decimal("1250.00"))
        self.assertEqual(response.context["supplier_total"], Decimal("800.00"))
        self.assertEqual(response.context["salary_total"], Decimal("22000.00"))
        self.assertEqual(response.context["projected_end_balance"], Decimal("78450.00"))

        chart = response.context["liquidity_chart"]
        self.assertEqual(len(chart["labels"]), 10)
        self.assertTrue(chart["labels"][0].startswith("v."))
        self.assertEqual(len(chart["projected_balance"]), 10)
        self.assertIn(1250.0, chart["incoming_invoices"])
        self.assertIn(800.0, chart["supplier_payments"])
        self.assertIn(22000.0, chart["salary_payments"])

    def test_default_selection_only_includes_193x_accounts_with_nonzero_balance(self):
        from bookkeeping.models import JournalEntry
        from bookkeeping.models import Transaction as TransactionModel

        # 1910 Kassa: not in the 193x group, so it should default to excluded even with a balance.
        petty_cash_account = create_account(self.company, "1910", "Kassa", AccountClass.ASSET)
        # 1935: in the 193x group but with a zero balance, so it should default to excluded.
        empty_bank_account = create_account(self.company, "1935", "Tomt bankkonto", AccountClass.ASSET)

        txn = TransactionModel.objects.create(
            accounting_year=self.year,
            date=timezone.localdate(),
            description="Kontantuttag",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=txn, account=petty_cash_account, debit=Decimal("500.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("500.00")
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        # Only self.bank_account (1930, non-zero balance) should be included by default.
        self.assertEqual(response.context["cash_balance"], Decimal("100000.00"))
        included_account_ids = [row["account"].pk for row in response.context["account_balances"]]
        self.assertEqual(included_account_ids, [self.bank_account.pk])
        self.assertEqual(response.context["excluded_liquidity_account_count"], 2)

    def test_excluded_account_is_omitted_from_cash_balance_and_forecast(self):
        savings_account = create_account(
            self.company, "1940", "Sparkonto", AccountClass.ASSET, include_in_liquidity_forecast=False
        )
        from bookkeeping.models import JournalEntry
        from bookkeeping.models import Transaction as TransactionModel

        savings_txn = TransactionModel.objects.create(
            accounting_year=self.year,
            date=timezone.localdate(),
            description="Sparat",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=savings_txn, account=savings_account, debit=Decimal("50000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=savings_txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("50000.00")
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cash_balance"], Decimal("100000.00"))
        self.assertEqual(response.context["excluded_liquidity_account_count"], 1)
        included_account_ids = [row["account"].pk for row in response.context["account_balances"]]
        self.assertIn(self.bank_account.pk, included_account_ids)
        self.assertNotIn(savings_account.pk, included_account_ids)


class LiquidityForecastAccountSelectionTests(CompanyTestCase):
    user_email = "liquidity-accounts@example.com"
    company_name = "Kontoval AB"
    company_org_number = "556600-1133"
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.savings_account = create_account(self.company, "1940", "Sparkonto", AccountClass.ASSET)

    def test_can_exclude_account_and_selection_persists(self):
        list_url = reverse("bookkeeping:liquidity_forecast_accounts")
        get_response = self.client.get(list_url)
        self.assertEqual(get_response.status_code, 200)

        formset = get_response.context["formset"]
        management_data = {
            "form-TOTAL_FORMS": str(formset.total_form_count()),
            "form-INITIAL_FORMS": str(formset.initial_form_count()),
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }
        form_data = {}
        for index, form in enumerate(formset.forms):
            form_data[f"form-{index}-id"] = str(form.instance.pk)
            if form.instance.pk != self.savings_account.pk:
                form_data[f"form-{index}-include_in_liquidity_forecast"] = "on"

        response = self.client.post(list_url, {**management_data, **form_data}, follow=True)
        self.assertEqual(response.status_code, 200)

        self.savings_account.refresh_from_db()
        self.bank_account.refresh_from_db()
        self.assertFalse(self.savings_account.include_in_liquidity_forecast)
        self.assertTrue(self.bank_account.include_in_liquidity_forecast)

        # The exclusion must persist across a fresh request (simulating a new session).
        dashboard_response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(dashboard_response.context["excluded_liquidity_account_count"], 1)


class DashboardInvoiceStatusChartTests(CompanyTestCase):
    """The chart splits invoices into paid and unpaid.

    Customer invoices used to be counted as unpaid unconditionally, on the premise
    that `Invoice` had no payment status - which stopped being true once it grew
    `is_paid`/`payment_date`. These pin both halves for both invoice types.
    """

    user_email = "dashboard-status@example.com"
    company_name = "Fakturastatus AB"
    company_org_number = "556600-7788"
    # The chart window is relative to today, so the year has to be this year.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.year = create_accounting_year(self.company, f"{today.year}-01-01", f"{today.year}-12-31")
        self.payable_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)

    def _customer_invoice(self, *, amount, due_date, is_paid=False, payment_date=None):
        from invoicing.models import Customer, Invoice, InvoiceLine

        customer, _ = Customer.objects.get_or_create(company=self.company, name="Kund AB")
        invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=due_date,
            due_date=due_date,
            is_paid=is_paid,
            payment_date=payment_date,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Tjänster",
            quantity=Decimal("1.00"),
            unit_price=amount,
            vat_rate=Decimal("0.00"),
        )
        return invoice

    def test_paid_customer_invoice_lands_in_the_paid_series_in_its_payment_week(self):
        today = timezone.localdate()
        self._customer_invoice(
            amount=Decimal("4000.00"),
            due_date=today - timedelta(days=14),
            is_paid=True,
            payment_date=today,
        )

        chart = self.client.get(reverse("bookkeeping:dashboard")).context["invoice_status_chart"]

        # Index 5 is the current week; the invoice fell due two weeks earlier but was
        # paid today, so it belongs to this week's paid bucket.
        self.assertEqual(chart["customer_paid"][5], 4000.0)
        self.assertEqual(sum(chart["customer_unpaid"]), 0.0)

    def test_unpaid_customer_invoice_lands_in_the_unpaid_series_in_its_due_week(self):
        today = timezone.localdate()
        self._customer_invoice(amount=Decimal("2500.00"), due_date=today + timedelta(days=7))

        chart = self.client.get(reverse("bookkeeping:dashboard")).context["invoice_status_chart"]

        self.assertEqual(sum(chart["customer_paid"]), 0.0)
        self.assertEqual(sum(chart["customer_unpaid"]), 2500.0)

    def test_supplier_invoices_are_split_the_same_way(self):
        from supplier_invoices.models import SupplierInvoice

        today = timezone.localdate()
        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier_name="Leverantör AB",
            invoice_date=today,
            due_date=today + timedelta(days=3),
            payable_account=self.payable_account,
            total_amount=Decimal("900.00"),
        )
        SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.year,
            supplier_name="Betald Leverantör AB",
            invoice_date=today,
            due_date=today - timedelta(days=20),
            payable_account=self.payable_account,
            total_amount=Decimal("300.00"),
            is_paid=True,
            payment_date=today,
        )

        chart = self.client.get(reverse("bookkeeping:dashboard")).context["invoice_status_chart"]

        self.assertEqual(sum(chart["supplier_unpaid"]), 900.0)
        self.assertEqual(chart["supplier_paid"][5], 300.0)


class ComplianceDashboardVatDriftTests(CompanyTestCase):
    user_email = "compliance-vat-drift@example.com"
    user_fields = {"is_staff": True}
    company_name = "Momsdrift AB"
    company_org_number = "556600-3344"
    accounting_year_dates = ("2026-01-01", "2026-12-31")

    def test_compliance_dashboard_is_blocked_for_non_finance_admin(self):
        non_staff = create_user("compliance-nonstaff@example.com")
        self.company.users.add(non_staff)
        self.client.force_login(non_staff)

        response = self.client.get(reverse("bookkeeping:compliance_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

    def test_dashboard_flags_snapshot_whose_fingerprint_no_longer_matches(self):
        from datetime import date

        from vat.models import VatCloseSnapshot
        from vat.services import build_vat_source_fingerprint

        intact_fingerprint, intact_ids = build_vat_source_fingerprint(
            company=self.company, start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
        )
        VatCloseSnapshot.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            source_transaction_ids=intact_ids,
            source_fingerprint=intact_fingerprint,
            closed_by=self.user,
        )
        drifted = VatCloseSnapshot.objects.create(
            company=self.company,
            accounting_year=self.year,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            source_fingerprint="0" * 64,
            closed_by=self.user,
        )

        response = self.client.get(reverse("bookkeeping:compliance_dashboard"))

        self.assertEqual(response.status_code, 200)
        flagged = response.context["vat_snapshot_drift"]
        self.assertEqual([snapshot.pk for snapshot in flagged], [drifted.pk])
        self.assertContains(response, "Momsstängningar där underlaget ändrats efter stängning")
