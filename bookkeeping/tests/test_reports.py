from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from bookkeeping.models import AccountClass, AccountingYear, JournalEntry, Transaction
from bookkeeping.reports import default_accounting_year
from saldovibe.testing import CompanyTestCase, create_account, create_accounting_year


class BalanceSheetCarryForwardTests(CompanyTestCase):
    user_email = "balance-sheet@example.com"
    company_name = "Balans AB"
    company_org_number = "181818-1818"
    # Two consecutive years, built below - the carry-forward is the point of the test.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.year_2017 = create_accounting_year(self.company, "2017-01-01", "2017-12-31")
        self.year_2018 = create_accounting_year(self.company, "2018-01-01", "2018-12-31")

        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.equity_account = create_account(self.company, "2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY)

        opening_txn = Transaction.objects.create(
            accounting_year=self.year_2017,
            date="2017-01-01",
            description="Startkapital",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=opening_txn, account=self.bank_account, debit=Decimal("50000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=opening_txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("50000.00")
        )

    def test_pdf_filename_with_non_ascii_characters_is_rfc5987_encoded(self):
        response = self.client.get(reverse("bookkeeping:balance_sheet_pdf"), {"year": self.year_2018.pk})

        self.assertEqual(response.status_code, 200)
        # Utan filename*-kodning föreslår webbläsaren "download.pdf" i stället.
        self.assertIn("filename*=utf-8''balansr%C3%A4kning", response["Content-Disposition"])

    def test_balance_sheet_for_2018_includes_carried_equity_account_2081(self):
        response = self.client.get(reverse("bookkeeping:balance_sheet"), {"year": self.year_2018.pk})

        self.assertEqual(response.status_code, 200)
        equity_numbers = [row["account"].number for row in response.context["equity"]]
        self.assertIn("2081", equity_numbers)
        self.assertEqual(response.context["total_equity"], Decimal("50000.00"))


class GeneralLedgerTests(CompanyTestCase):
    user_email = "huvudbok@example.com"
    company_name = "Huvudbok AB"
    company_org_number = "191919-1919"
    # Two consecutive years - the IB carry-forward is the point of the test.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.year_2017 = create_accounting_year(self.company, "2017-01-01", "2017-12-31")
        self.year_2018 = create_accounting_year(self.company, "2018-01-01", "2018-12-31")

        self.bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.equity_account = create_account(self.company, "2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY)
        self.revenue_account = create_account(self.company, "3001", "Försäljning", AccountClass.REVENUE)

        opening_txn = Transaction.objects.create(
            accounting_year=self.year_2017,
            date="2017-01-01",
            description="Startkapital",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=opening_txn, account=self.bank_account, debit=Decimal("50000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=opening_txn, account=self.equity_account, debit=Decimal("0.00"), credit=Decimal("50000.00")
        )

        sale_txn = Transaction.objects.create(
            accounting_year=self.year_2018,
            date="2018-03-01",
            description="Försäljning",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=sale_txn, account=self.bank_account, debit=Decimal("1000.00"), credit=Decimal("0.00")
        )
        JournalEntry.objects.create(
            transaction=sale_txn, account=self.revenue_account, debit=Decimal("0.00"), credit=Decimal("1000.00")
        )

    def test_general_ledger_shows_opening_balance_running_balance_and_closing_balance(self):
        response = self.client.get(reverse("bookkeeping:general_ledger"), {"year": self.year_2018.pk})

        self.assertEqual(response.status_code, 200)
        items = {item["account"].number: item for item in response.context["ledger_accounts"]}

        bank = items["1930"]
        self.assertEqual(bank["opening_balance"], Decimal("50000.00"))
        self.assertEqual(len(bank["rows"]), 1)
        self.assertEqual(bank["rows"][0]["balance"], Decimal("51000.00"))
        self.assertEqual(bank["closing_balance"], Decimal("51000.00"))

        # Balance account without movement this year still appears with its IB.
        equity = items["2081"]
        self.assertEqual(equity["opening_balance"], Decimal("-50000.00"))
        self.assertEqual(equity["rows"], [])

        # Result account starts the year at zero.
        revenue = items["3001"]
        self.assertEqual(revenue["opening_balance"], Decimal("0"))
        self.assertEqual(revenue["closing_balance"], Decimal("-1000.00"))

        self.assertEqual(response.context["total_debit"], Decimal("1000.00"))
        self.assertEqual(response.context["total_credit"], Decimal("1000.00"))

    def test_general_ledger_pdf_downloads(self):
        response = self.client.get(reverse("bookkeeping:general_ledger_pdf"), {"year": self.year_2018.pk})

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"%PDF"))


class ReskontraTests(CompanyTestCase):
    user_email = "reskontra@example.com"
    company_name = "Reskontra AB"
    company_org_number = "202020-2020"
    # The report always runs "per today", so the year has to straddle today.
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.accounting_year = create_accounting_year(
            self.company, self.today - timedelta(days=200), self.today + timedelta(days=165)
        )

        create_account(self.company, "1510", "Kundfordringar", AccountClass.ASSET)
        create_account(self.company, "2611", "Utgående moms 25%", AccountClass.EQUITY_LIABILITY)
        self.revenue_account = create_account(self.company, "3001", "Försäljning", AccountClass.REVENUE)
        self.expense_account = create_account(self.company, "4000", "Inköp", AccountClass.COST_OF_GOODS)
        self.input_vat_account = create_account(self.company, "2641", "Ingående moms", AccountClass.EQUITY_LIABILITY)
        self.payable_account = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)

        from invoicing.models import Article, Customer, Invoice, InvoiceLine

        customer = Customer.objects.create(company=self.company, name="Kund AB")
        article = Article.objects.create(
            company=self.company,
            name="Konsulttimme",
            unit_price=Decimal("1000.00"),
            income_account=self.revenue_account,
        )
        self.invoice = Invoice.objects.create(
            company=self.company,
            customer=customer,
            invoice_date=self.today - timedelta(days=40),
            due_date=self.today - timedelta(days=10),
        )
        InvoiceLine.objects.create(
            invoice=self.invoice,
            article=article,
            description="Konsultarbete",
            quantity=Decimal("1.00"),
            unit="tim",
            unit_price=Decimal("1000.00"),
            vat_rate=Decimal("25.00"),
            sort_order=0,
        )
        self.invoice.bookkeep(self.user)

        from supplier_invoices.models import Supplier, SupplierInvoice
        from supplier_invoices.services import register_and_bookkeep_supplier_invoice

        supplier = Supplier.objects.create(company=self.company, name="Lev AB")
        self.supplier_invoice = SupplierInvoice.objects.create(
            company=self.company,
            accounting_year=self.accounting_year,
            supplier=supplier,
            supplier_name=supplier.name,
            invoice_number="LEV-001",
            invoice_date=self.today - timedelta(days=120),
            due_date=self.today - timedelta(days=95),
            expense_account=self.expense_account,
            vat_account=self.input_vat_account,
            payable_account=self.payable_account,
            amount_ex_vat=Decimal("400.00"),
            vat_amount=Decimal("100.00"),
            total_amount=Decimal("500.00"),
            created_by=self.user,
        )
        register_and_bookkeep_supplier_invoice(self.supplier_invoice, self.user)

    def test_reskontra_lists_open_invoices_with_aging_and_reconciles_against_ledger(self):
        response = self.client.get(reverse("bookkeeping:reskontra"))

        self.assertEqual(response.status_code, 200)

        customer_side = response.context["customer_side"]
        self.assertEqual(len(customer_side["rows"]), 1)
        self.assertEqual(customer_side["rows"][0]["remaining"], Decimal("1250.00"))
        self.assertTrue(customer_side["rows"][0]["is_overdue"])
        # 10 days overdue lands in the 1-30 bucket; posting and reskontra must agree.
        self.assertEqual(customer_side["buckets"][1]["total"], Decimal("1250.00"))
        self.assertEqual(customer_side["ledger_total"], Decimal("1250.00"))
        self.assertEqual(customer_side["difference"], Decimal("0.00"))

        supplier_side = response.context["supplier_side"]
        self.assertEqual(len(supplier_side["rows"]), 1)
        self.assertEqual(supplier_side["rows"][0]["remaining"], Decimal("500.00"))
        # 95 days overdue lands in the over-90 bucket.
        self.assertEqual(supplier_side["buckets"][4]["total"], Decimal("500.00"))
        self.assertEqual(supplier_side["ledger_total"], Decimal("500.00"))
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

    def test_paid_invoices_disappear_from_the_reskontra(self):
        from bookkeeping.payables import register_manual_payment

        bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        register_manual_payment(
            self.supplier_invoice,
            self.user,
            payment_date=self.today,
            amount=Decimal("500.00"),
            payment_account=bank_account,
        )

        response = self.client.get(reverse("bookkeeping:reskontra"))

        supplier_side = response.context["supplier_side"]
        self.assertEqual(supplier_side["rows"], [])
        self.assertEqual(supplier_side["reskontra_total"], Decimal("0.00"))
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

    def test_reskontra_per_historic_date_shows_state_as_of_that_date(self):
        from bookkeeping.payables import register_manual_payment

        bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        register_manual_payment(
            self.supplier_invoice,
            self.user,
            payment_date=self.today,
            amount=Decimal("500.00"),
            payment_account=bank_account,
        )

        # The day before the payment the invoice was still open, and the payment
        # voucher (dated today) is excluded from the ledger side too.
        yesterday = (self.today - timedelta(days=1)).isoformat()
        response = self.client.get(reverse("bookkeeping:reskontra"), {"date": yesterday})

        self.assertEqual(response.context["report_date"].isoformat(), yesterday)
        supplier_side = response.context["supplier_side"]
        self.assertEqual(len(supplier_side["rows"]), 1)
        self.assertEqual(supplier_side["rows"][0]["remaining"], Decimal("500.00"))
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

        # Before the invoice date nothing is in the reskontra, and the ledger agrees.
        before_invoice = (self.today - timedelta(days=150)).isoformat()
        response = self.client.get(reverse("bookkeeping:reskontra"), {"date": before_invoice})

        supplier_side = response.context["supplier_side"]
        self.assertEqual(supplier_side["rows"], [])
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

    def test_reskontra_counts_reversed_payment_on_dates_before_the_reversal(self):
        from banking.services import undo_bank_payment
        from bookkeeping.payables import register_manual_payment

        bank_account = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        payment_txn = register_manual_payment(
            self.supplier_invoice,
            self.user,
            payment_date=self.today - timedelta(days=10),
            amount=Decimal("500.00"),
            payment_account=bank_account,
        )
        undo_bank_payment(payment_txn, user=self.user, company=self.company)

        # Between the payment and the reversal (dated today) the invoice was paid,
        # and the ledger side cuts off on voucher dates — the sides must agree.
        report_date = (self.today - timedelta(days=5)).isoformat()
        response = self.client.get(reverse("bookkeeping:reskontra"), {"date": report_date})

        supplier_side = response.context["supplier_side"]
        self.assertEqual(supplier_side["rows"], [])
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

        # Per today the reversal has happened, so the invoice is open again.
        response = self.client.get(reverse("bookkeeping:reskontra"))
        supplier_side = response.context["supplier_side"]
        self.assertEqual(len(supplier_side["rows"]), 1)
        self.assertEqual(supplier_side["rows"][0]["remaining"], Decimal("500.00"))
        self.assertEqual(supplier_side["difference"], Decimal("0.00"))

    def test_reskontra_pdf_downloads(self):
        response = self.client.get(reverse("bookkeeping:reskontra_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"%PDF"))


class SystemDocumentationTests(CompanyTestCase):
    user_email = "sysdoc@example.com"
    user_fields = {"is_staff": True}
    company_name = "Systemdoc AB"
    company_org_number = "556677-4455"
    accounting_year_dates = None

    def setUp(self):
        super().setUp()
        # The rendered kontoplan section has to show a real account number.
        create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)

    def test_system_documentation_page_includes_all_bfn_kap9_sections(self):
        from bookkeeping.models import VoucherSeriesRule

        VoucherSeriesRule.seed_defaults_for_company(self.company)

        response = self.client.get(reverse("bookkeeping:system_documentation"))

        self.assertEqual(response.status_code, 200)
        for marker in (
            "Kontoplan",
            "Samlingsplan",
            "Verifikationsserier",
            "Verifieringskedjor",
            "Behandlingsregler",
            "Informationsflöden",
            "Behandlingshistorik",
            "Arkivplan",
            "1930",
        ):
            self.assertContains(response, marker)

    def test_system_documentation_shows_last_change_to_voucher_series_rule(self):
        from bookkeeping.models import TransactionSource, VoucherSeriesRule

        VoucherSeriesRule.seed_defaults_for_company(self.company)
        rule = VoucherSeriesRule.objects.get(company=self.company, source=TransactionSource.BANK)
        rule.series_code = "K"
        rule.save()

        response = self.client.get(reverse("bookkeeping:system_documentation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "K")
        self.assertContains(response, self.user.get_full_name() or self.user.email)

    def test_system_documentation_pdf_downloads(self):
        response = self.client.get(reverse("bookkeeping:system_documentation_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"%PDF"))


class DefaultAccountingYearTests(CompanyTestCase):
    user_email = "year-default@example.com"
    company_name = "Årsdefault AB"
    accounting_year_dates = None

    def test_prefers_year_containing_today_over_future_year(self):
        today = timezone.localdate()
        current = create_accounting_year(self.company, f"{today.year}-01-01", f"{today.year}-12-31")
        create_accounting_year(self.company, f"{today.year + 1}-01-01", f"{today.year + 1}-12-31")

        years = AccountingYear.objects.filter(company=self.company)
        self.assertEqual(default_accounting_year(years), current)

    def test_falls_back_to_latest_year_when_none_contains_today(self):
        today = timezone.localdate()
        create_accounting_year(self.company, f"{today.year - 3}-01-01", f"{today.year - 3}-12-31")
        newest_past = create_accounting_year(self.company, f"{today.year - 2}-01-01", f"{today.year - 2}-12-31")

        years = AccountingYear.objects.filter(company=self.company)
        self.assertEqual(default_accounting_year(years), newest_past)
