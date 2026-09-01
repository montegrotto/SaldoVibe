from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bookkeeping.models import Account, AccountClass, AccountingYear, Company, JournalEntry, Transaction
from saldovibe.testing import create_company, create_user, set_active_company
from vat.models import VatCloseSnapshot
from vat.services import (
    SKATTEVERKET_FIELD_CODES,
    ZERO,
    build_closed_periods,
    build_vat_periods,
    get_skatteverket_field_groups,
    round_to_whole_krona,
    validate_eskd_export,
)


class VatRoundingTests(SimpleTestCase):
    def test_rounds_down_never_to_nearest_or_up(self):
        self.assertEqual(round_to_whole_krona("150.01"), 150)
        self.assertEqual(round_to_whole_krona("150.50"), 150)
        self.assertEqual(round_to_whole_krona("150.99"), 150)
        self.assertEqual(round_to_whole_krona("150.00"), 150)

    def test_negative_amounts_round_down_away_from_zero(self):
        self.assertEqual(round_to_whole_krona("-150.01"), -151)

    def test_none_and_zero_are_treated_as_zero(self):
        self.assertEqual(round_to_whole_krona(None), 0)
        self.assertEqual(round_to_whole_krona(ZERO), 0)


class ReverseChargeVatFieldCodeTests(SimpleTestCase):
    def test_boxes_30_31_32_follow_the_vat_rate(self):
        from bookkeeping.bas_accounts import lookup_bas_account

        # Ruta 30/31/32 = utgående moms på inköp 25/12/6 %, for both omvänd
        # skattskyldighet (261x/263x) and import (26x5).
        expected = {"2614": "30", "2615": "30", "2624": "31", "2625": "31", "2634": "32", "2635": "32"}
        for number, code in expected.items():
            self.assertEqual(lookup_bas_account(number)["vat_field_code"], code, number)


class VatFieldGroupingTests(SimpleTestCase):
    def test_all_field_codes_are_covered_exactly_once(self):
        groups = get_skatteverket_field_groups({code: ZERO for code in SKATTEVERKET_FIELD_CODES})

        grouped_codes = [row["code"] for group in groups for row in group["rows"]]
        self.assertEqual(sorted(grouped_codes), sorted(SKATTEVERKET_FIELD_CODES))
        self.assertEqual(len(grouped_codes), len(set(grouped_codes)))

    def test_amounts_are_carried_into_the_grouped_rows_rounded_down_to_whole_krona(self):
        vat_boxes = {code: ZERO for code in SKATTEVERKET_FIELD_CODES}
        # 150.99 must round down to 150, not to nearest (151) or up.
        vat_boxes["49"] = "150.99"

        groups = get_skatteverket_field_groups(vat_boxes)

        payable_group = next(group for group in groups if group["letter"] == "G")
        box_49 = next(row for row in payable_group["rows"] if row["code"] == "49")
        self.assertEqual(box_49["amount"], 150)


class VatGroupCCalculationTests(TestCase):
    def setUp(self):
        self.user = create_user("group-c-user@example.com", is_staff=True)
        self.client.force_login(self.user)

    def _set_active_company(self, company):
        set_active_company(self.client, company)

    def _assert_contains_amount(self, response, amount):
        self.assertContains(response, f"{amount:,}".replace(",", "\xa0"))

    def test_purchase_basis_boxes_use_debit_minus_credit_and_reach_the_report_and_export(self):
        company = create_company(
            "Reverse Charge AB",
            "556100-0012",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        eu_goods_purchase = Account.objects.create(
            company=company,
            number="4515",
            name="Inköp av råvaror och material från annat EU-land",
            account_class=AccountClass.COST_OF_GOODS,
            vat_field_code="20",
            is_active=True,
        )
        supplier_debt = Account.objects.create(
            company=company,
            number="2440",
            name="Leverantörsskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        # A credit note against the same box should net off against the purchase.
        credit_note_txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-10",
            description="Kreditnota EU-inköp",
            created_by=self.user,
        )
        JournalEntry.objects.create(
            transaction=credit_note_txn, account=eu_goods_purchase, debit="0.00", credit="200.00"
        )
        JournalEntry.objects.create(transaction=credit_note_txn, account=supplier_debt, debit="200.00", credit="0.00")

        purchase_txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="EU-inköp material",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=purchase_txn, account=eu_goods_purchase, debit="1000.00", credit="0.00")
        JournalEntry.objects.create(transaction=purchase_txn, account=supplier_debt, debit="0.00", credit="1000.00")

        period_key = "2026-01-01:2026-01-31"
        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk, "period": period_key})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Momspliktiga inköp där köparen är skattskyldig")
        # Debit (1000) minus credit (200) = net 800.00, not the raw debit total.
        self._assert_contains_amount(response, 800)

        export_response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": period_key},
        )
        self.assertEqual(export_response.status_code, 200)
        content = export_response.content.decode("iso-8859-1")
        self.assertIn("<InkopVaruAnnatEg>800</InkopVaruAnnatEg>", content)

    def test_box_37_trepartshandel_maps_to_the_purchase_tag_not_the_sale_tag(self):
        # Box 37 is labelled "Mellanmans INKÖP av varor vid trepartshandel" (a purchase),
        # so it must land on <InkopVaruMellan3p>, never <ForsVaruMellan3p>.
        company = create_company(
            "Trepartshandel AB",
            "556100-0013",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        middleman_purchase = Account.objects.create(
            company=company,
            number="4536",
            name="Mellanmans inköp vid trepartshandel",
            account_class=AccountClass.COST_OF_GOODS,
            vat_field_code="37",
            is_active=True,
        )
        supplier_debt = Account.objects.create(
            company=company,
            number="2440",
            name="Leverantörsskulder",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Trepartshandel inköp",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=middleman_purchase, debit="500.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=supplier_debt, debit="0.00", credit="500.00")

        export_response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": "2026-01-01:2026-01-31"},
        )
        self.assertEqual(export_response.status_code, 200)
        content = export_response.content.decode("iso-8859-1")
        self.assertIn("<InkopVaruMellan3p>500</InkopVaruMellan3p>", content)
        self.assertIn("<ForsVaruMellan3p>0</ForsVaruMellan3p>", content)

    def test_refund_period_reports_box_50_in_the_export_instead_of_being_dropped(self):
        company = create_company(
            "Refund AB",
            "556100-0014",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        output_vat = Account.objects.create(
            company=company,
            number="2611",
            name="Utgående moms 25%",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="10",
            is_active=True,
        )
        input_vat = Account.objects.create(
            company=company,
            number="2641",
            name="Debiterad ingående moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="48",
            is_active=True,
        )
        bank = Account.objects.create(
            company=company,
            number="1930",
            name="Företagskonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )
        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Stort inköp, momsöverskott",
            created_by=self.user,
        )
        # Output VAT 100, input VAT 500 -> net -400 -> nothing to pay, 400 to refund.
        JournalEntry.objects.create(transaction=txn, account=output_vat, debit="0.00", credit="100.00")
        JournalEntry.objects.create(transaction=txn, account=input_vat, debit="500.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=bank, debit="0.00", credit="400.00")

        export_response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": "2026-01-01:2026-01-31"},
        )
        self.assertEqual(export_response.status_code, 200)
        content = export_response.content.decode("iso-8859-1")
        self.assertIn("<MomsBetala>0</MomsBetala>", content)
        self.assertIn("<MomsFaTillbaka>400</MomsFaTillbaka>", content)


class VatPeriodBuilderTests(SimpleTestCase):
    def _year(self, start, end):
        return AccountingYear(start_date=date.fromisoformat(start), end_date=date.fromisoformat(end))

    def test_current_period_is_marked_ongoing_and_future_periods_are_excluded(self):
        year = self._year("2026-01-01", "2026-12-31")

        periods = build_vat_periods(year, "monthly", today=date(2026, 3, 15))

        statuses = {period["key"]: period["status"] for period in periods}
        self.assertEqual(statuses["2026-01-01:2026-01-31"], "closed")
        self.assertEqual(statuses["2026-02-01:2026-02-28"], "closed")
        self.assertEqual(statuses["2026-03-01:2026-03-31"], "ongoing")
        self.assertNotIn("2026-04-01:2026-04-30", statuses)

    def test_periods_entirely_before_vat_start_date_are_excluded(self):
        year = self._year("2026-01-01", "2026-12-31")

        periods = build_vat_periods(
            year,
            "quarterly",
            today=date(2026, 7, 15),
            vat_start_date=date(2026, 4, 15),
        )

        keys = [period["key"] for period in periods]
        self.assertNotIn("2026-01-01:2026-03-31", keys)
        self.assertIn("2026-04-01:2026-06-30", keys)
        self.assertIn("2026-07-01:2026-09-30", keys)

    def test_build_closed_periods_excludes_the_ongoing_period(self):
        year = self._year("2026-01-01", "2026-12-31")

        closed = build_closed_periods(year, "monthly", today=date(2026, 3, 15))

        keys = [period["key"] for period in closed]
        self.assertNotIn("2026-03-01:2026-03-31", keys)
        self.assertIn("2026-02-01:2026-02-28", keys)


class VatReportingTests(TestCase):
    def setUp(self):
        self.user = create_user("vat-user@example.com", is_staff=True)
        self.client.force_login(self.user)

    def _set_active_company(self, company):
        set_active_company(self.client, company)

    def _assert_contains_amount(self, response, amount):
        self.assertContains(response, f"{amount:,}".replace(",", "\xa0"))

    def _assert_not_contains_amount(self, response, amount):
        self.assertNotContains(response, f"{amount:,}".replace(",", "\xa0"))

    def test_close_and_eskd_export_are_blocked_for_non_finance_admin(self):
        non_staff = create_user("vat-nonstaff@example.com")
        self.client.force_login(non_staff)

        response = self.client.post(reverse("vat:close_period"), {"year": 1, "period": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

        response = self.client.get(reverse("vat:export_skatteverket"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bookkeeping:dashboard"))

    def test_vat_nav_is_hidden_when_reporting_is_disabled(self):
        company = create_company(
            "No VAT AB",
            "556100-0001",
            vat_reporting_period=Company.VatReportingPeriod.NONE,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        response = self.client.get(reverse("bookkeeping:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("vat:report"))

    def test_vat_report_redirects_when_reporting_is_disabled(self):
        company = create_company(
            "Disabled VAT AB",
            "556100-0002",
            vat_reporting_period=Company.VatReportingPeriod.NONE,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        response = self.client.get(reverse("vat:report"))

        self.assertRedirects(response, reverse("bookkeeping:dashboard"))

    def test_vat_report_and_skatteverket_export_for_closed_period(self):
        company = create_company(
            "VAT Enabled AB",
            "556100-0003",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        output_vat = Account.objects.create(
            company=company,
            number="2611",
            name="Utgaende moms 25%",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="10",
            is_active=True,
        )
        sales_25 = Account.objects.create(
            company=company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            vat_field_code="05",
            is_active=True,
        )
        input_vat = Account.objects.create(
            company=company,
            number="2641",
            name="Debiterad ingaende moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="48",
            is_active=True,
        )
        bank = Account.objects.create(
            company=company,
            number="1930",
            name="Foretagskonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )

        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Momsperiod januari",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=sales_25, debit="0.00", credit="1000.00")
        JournalEntry.objects.create(transaction=txn, account=output_vat, debit="0.00", credit="250.00")
        JournalEntry.objects.create(transaction=txn, account=input_vat, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=bank, debit="1150.00", credit="0.00")

        period_key = "2026-01-01:2026-01-31"
        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk, "period": period_key})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Momsdeklaration")
        self.assertContains(response, "Valideringsstatus")
        self.assertContains(response, "Fältkod")
        self.assertContains(response, "05")
        self.assertContains(response, "10")
        self.assertContains(response, "48")
        self.assertContains(response, "Momspliktig försäljning eller uttag exklusive moms")
        self.assertContains(response, "Ingående moms att dra av")
        self.assertContains(response, "Moms att betala eller få tillbaka")
        self._assert_contains_amount(response, 1000)
        self._assert_contains_amount(response, 250)
        self._assert_contains_amount(response, 100)
        self.assertContains(response, "Visa verifikationer")

        export_response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": period_key},
        )

        self.assertEqual(export_response.status_code, 200)
        self.assertIn("application/xml", export_response["Content-Type"])
        content = export_response.content.decode("iso-8859-1")
        self.assertIn('<eSKDUpload Version="6.0">', content)
        self.assertIn("<Period>202601</Period>", content)
        self.assertIn("<ForsMomsEjAnnan>1000</ForsMomsEjAnnan>", content)
        self.assertIn("<MomsUtgHog>250</MomsUtgHog>", content)
        self.assertIn("<MomsIngAvdr>100</MomsIngAvdr>", content)
        self.assertIn("<MomsBetala>150</MomsBetala>", content)

        field_05_response = self.client.get(
            reverse("vat:field_transactions", args=["05"]),
            {"year": accounting_year.pk, "period": period_key},
        )
        self.assertEqual(field_05_response.status_code, 200)
        self.assertContains(field_05_response, "Momsperiod januari")
        self.assertContains(
            field_05_response,
            f"return_to=/moms/falt/05/%3Fyear%3D{accounting_year.pk}%26period%3D",
        )

        field_response = self.client.get(
            reverse("vat:field_transactions", args=["10"]),
            {"year": accounting_year.pk, "period": period_key},
        )
        self.assertEqual(field_response.status_code, 200)
        self.assertContains(field_response, "Momsperiod januari")

    def test_vat_close_is_blocked_when_period_is_locked(self):
        from bookkeeping.models import PeriodLock

        company = create_company(
            "VAT Locked AB",
            "556100-0016",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        Account.objects.create(
            company=company,
            number="2650",
            name="Redovisningskonto for moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        PeriodLock.objects.create(
            company=company,
            accounting_year=accounting_year,
            period_start="2026-01-01",
            period_end="2026-01-31",
            is_locked=True,
            reason="Bokslut januari",
        )

        response = self.client.post(
            reverse("vat:close_period"),
            {"year": accounting_year.pk, "period": "2026-01-01:2026-01-31"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(reference="VATCLOSE:2026-01-01:2026-01-31").exists())

    def test_vat_period_can_be_closed_by_creating_transaction(self):
        company = create_company(
            "VAT Close AB",
            "556100-0006",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        output_vat = Account.objects.create(
            company=company,
            number="2611",
            name="Utgaende moms 25%",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="10",
            is_active=True,
        )
        input_vat = Account.objects.create(
            company=company,
            number="2641",
            name="Debiterad ingaende moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="48",
            is_active=True,
        )
        settlement = Account.objects.create(
            company=company,
            number="2650",
            name="Redovisningskonto for moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            is_active=True,
        )
        sales_25 = Account.objects.create(
            company=company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            vat_field_code="05",
            is_active=True,
        )
        bank = Account.objects.create(
            company=company,
            number="1930",
            name="Foretagskonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )

        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Momsperiod januari",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=sales_25, debit="0.00", credit="1000.00")
        JournalEntry.objects.create(transaction=txn, account=output_vat, debit="0.00", credit="250.00")
        JournalEntry.objects.create(transaction=txn, account=input_vat, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=bank, debit="1150.00", credit="0.00")

        period_key = "2026-01-01:2026-01-31"
        response = self.client.post(
            reverse("vat:close_period"),
            {"year": accounting_year.pk, "period": period_key},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("vat:report") + f"?year={accounting_year.pk}&period={period_key}")

        closing_txn = Transaction.objects.get(reference="VATCLOSE:2026-01-01:2026-01-31")
        self.assertEqual(closing_txn.date.isoformat(), "2026-01-31")
        self.assertEqual(closing_txn.entries.count(), 3)

        output_entry = closing_txn.entries.get(account=output_vat)
        self.assertEqual(output_entry.debit, 250)
        self.assertEqual(output_entry.credit, 0)

        input_entry = closing_txn.entries.get(account=input_vat)
        self.assertEqual(input_entry.debit, 0)
        self.assertEqual(input_entry.credit, 100)

        settlement_entry = closing_txn.entries.get(account=settlement)
        self.assertEqual(settlement_entry.debit, 0)
        self.assertEqual(settlement_entry.credit, 150)

        snapshot = VatCloseSnapshot.objects.get(
            company=company,
            accounting_year=accounting_year,
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        self.assertEqual(snapshot.closed_transaction_id, closing_txn.pk)
        self.assertEqual(len(snapshot.source_fingerprint), 64)
        self.assertIn(txn.pk, snapshot.source_transaction_ids)
        self.assertEqual(snapshot.vat_boxes["10"], "250.00")

        self.assertTrue(Transaction.objects.filter(pk=closing_txn.pk).exists())

        from django.contrib.messages import get_messages

        message_texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("Periodlåsning" in text for text in message_texts),
            message_texts,
        )

    def test_skatteverket_export_is_blocked_when_org_number_is_invalid(self):
        company = create_company(
            "Invalid Org AB",
            "BAD-ORG",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        Account.objects.create(
            company=company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            vat_field_code="05",
            is_active=True,
        )

        period_key = "2026-01-01:2026-01-31"
        response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": period_key},
        )

        self.assertRedirects(
            response,
            reverse("vat:report") + f"?year={accounting_year.pk}&period={period_key}",
            fetch_redirect_response=False,
        )

    def test_vat_report_falls_back_to_standard_account_numbers_when_vat_code_is_blank(self):
        company = create_company(
            "Fallback VAT AB",
            "556100-0004",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        output_vat = Account.objects.create(
            company=company,
            number="2611",
            name="Utgaende moms 25%",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="",
            is_active=True,
        )
        sales_25 = Account.objects.create(
            company=company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            vat_field_code="",
            is_active=True,
        )
        input_vat = Account.objects.create(
            company=company,
            number="2641",
            name="Debiterad ingaende moms",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="",
            is_active=True,
        )
        bank = Account.objects.create(
            company=company,
            number="1930",
            name="Foretagskonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )

        txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Fallback momsperiod januari",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=txn, account=sales_25, debit="0.00", credit="1000.00")
        JournalEntry.objects.create(transaction=txn, account=output_vat, debit="0.00", credit="250.00")
        JournalEntry.objects.create(transaction=txn, account=input_vat, debit="100.00", credit="0.00")
        JournalEntry.objects.create(transaction=txn, account=bank, debit="1150.00", credit="0.00")

        period_key = "2026-01-01:2026-01-31"
        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk, "period": period_key})

        self.assertEqual(response.status_code, 200)
        self._assert_contains_amount(response, 1000)
        self._assert_contains_amount(response, 250)
        self._assert_contains_amount(response, 100)

    def test_vat_start_date_excludes_transactions_before_company_vat_start(self):
        company = create_company(
            "VAT Start AB",
            "556100-0005",
            vat_reporting_period=Company.VatReportingPeriod.QUARTERLY,
            vat_start_date="2026-02-01",
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        output_vat = Account.objects.create(
            company=company,
            number="2611",
            name="Utgaende moms 25%",
            account_class=AccountClass.EQUITY_LIABILITY,
            vat_field_code="10",
            is_active=True,
        )
        sales_25 = Account.objects.create(
            company=company,
            number="3041",
            name="Forsaljning tjanster 25%",
            account_class=AccountClass.REVENUE,
            vat_field_code="05",
            is_active=True,
        )
        bank = Account.objects.create(
            company=company,
            number="1930",
            name="Foretagskonto",
            account_class=AccountClass.ASSET,
            is_active=True,
        )

        january_txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-01-15",
            description="Januari moms",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=january_txn, account=sales_25, debit="0.00", credit="1000.00")
        JournalEntry.objects.create(transaction=january_txn, account=output_vat, debit="0.00", credit="250.00")
        JournalEntry.objects.create(transaction=january_txn, account=bank, debit="1250.00", credit="0.00")

        february_txn = Transaction.objects.create(
            accounting_year=accounting_year,
            date="2026-02-10",
            description="Februari moms",
            created_by=self.user,
        )
        JournalEntry.objects.create(transaction=february_txn, account=sales_25, debit="0.00", credit="600.00")
        JournalEntry.objects.create(transaction=february_txn, account=output_vat, debit="0.00", credit="150.00")
        JournalEntry.objects.create(transaction=february_txn, account=bank, debit="750.00", credit="0.00")

        period_key = "2026-01-01:2026-03-31"
        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk, "period": period_key})

        self.assertEqual(response.status_code, 200)
        self._assert_contains_amount(response, 600)
        self._assert_contains_amount(response, 150)
        self._assert_not_contains_amount(response, 1000)
        self._assert_not_contains_amount(response, 250)

    def test_vat_overview_excludes_periods_entirely_before_reporting_start(self):
        company = create_company(
            "Pre-start VAT AB",
            "556100-0008",
            vat_reporting_period=Company.VatReportingPeriod.QUARTERLY,
            vat_start_date="2020-04-15",
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date="2020-01-01",
            end_date="2020-12-31",
        )

        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk})

        self.assertEqual(response.status_code, 200)
        period_keys = [period["key"] for period in response.context["periods"]]
        self.assertNotIn("2020-01-01:2020-03-31", period_keys)
        self.assertIn("2020-04-01:2020-06-30", period_keys)

    def test_vat_overview_excludes_accounting_years_entirely_before_reporting_start(self):
        company = create_company(
            "Old Years VAT AB",
            "556100-0011",
            vat_reporting_period=Company.VatReportingPeriod.QUARTERLY,
            vat_start_date="2026-07-01",
        )
        company.users.add(self.user)
        self._set_active_company(company)

        old_year = AccountingYear.objects.create(
            company=company,
            start_date="2025-01-01",
            end_date="2025-12-31",
        )
        current_year = AccountingYear.objects.create(
            company=company,
            start_date="2026-01-01",
            end_date="2026-12-31",
        )

        response = self.client.get(reverse("vat:report"))

        self.assertEqual(response.status_code, 200)
        year_ids = [year.pk for year in response.context["years"]]
        self.assertNotIn(old_year.pk, year_ids)
        self.assertIn(current_year.pk, year_ids)
        self.assertEqual(response.context["selected_year"].pk, current_year.pk)

    def test_ongoing_period_is_visible_but_cannot_be_closed_or_exported(self):
        today = date.today()
        company = create_company(
            "Ongoing VAT AB",
            "556100-0009",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date=date(today.year, 1, 1),
            end_date=date(today.year, 12, 31),
        )

        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk})
        self.assertEqual(response.status_code, 200)

        periods = response.context["periods"]
        ongoing_periods = [period for period in periods if period["status"] == "ongoing"]
        self.assertEqual(len(ongoing_periods), 1)
        ongoing_key = ongoing_periods[0]["key"]
        self.assertContains(response, "pågående")

        close_response = self.client.post(
            reverse("vat:close_period"),
            {"year": accounting_year.pk, "period": ongoing_key},
        )
        self.assertRedirects(close_response, reverse("vat:report") + f"?year={accounting_year.pk}")
        self.assertFalse(Transaction.objects.filter(reference__startswith="VATCLOSE:").exists())

        export_response = self.client.get(
            reverse("vat:export_skatteverket"),
            {"year": accounting_year.pk, "period": ongoing_key},
        )
        self.assertRedirects(export_response, reverse("vat:report") + f"?year={accounting_year.pk}")

    def test_period_dropdown_orders_unreported_closed_period_before_ongoing_period(self):
        today = date.today()
        company = create_company(
            "Sort Order VAT AB",
            "556100-0010",
            vat_reporting_period=Company.VatReportingPeriod.MONTHLY,
        )
        company.users.add(self.user)
        self._set_active_company(company)

        accounting_year = AccountingYear.objects.create(
            company=company,
            start_date=date(today.year, 1, 1),
            end_date=date(today.year, 12, 31),
        )

        response = self.client.get(reverse("vat:report"), {"year": accounting_year.pk})
        self.assertEqual(response.status_code, 200)
        periods = response.context["periods"]

        index_by_status_and_report = {}
        for index, period in enumerate(periods):
            if period["status"] == "ongoing":
                index_by_status_and_report.setdefault("ongoing", index)
            elif not period["is_reported"]:
                index_by_status_and_report.setdefault("unreported_closed", index)
            else:
                index_by_status_and_report.setdefault("reported_closed", index)

        if "unreported_closed" in index_by_status_and_report and "ongoing" in index_by_status_and_report:
            self.assertLess(
                index_by_status_and_report["unreported_closed"],
                index_by_status_and_report["ongoing"],
            )
        if "ongoing" in index_by_status_and_report and "reported_closed" in index_by_status_and_report:
            self.assertLess(
                index_by_status_and_report["ongoing"],
                index_by_status_and_report["reported_closed"],
            )

        # The default selected period (no ?period= given) should be the top of the sorted list.
        self.assertEqual(response.context["selected_period"]["key"], periods[0]["key"])


class EskdExportValidationTests(SimpleTestCase):
    """`validate_eskd_export` is the last gate before a VAT return is handed to
    Skatteverket, so each way it can refuse needs to actually refuse. Pure
    function over an unsaved Company - no database needed.
    """

    @staticmethod
    def _complete_boxes(**overrides):
        boxes = {code: ZERO for code in SKATTEVERKET_FIELD_CODES}
        boxes.update(overrides)
        return boxes

    def test_a_complete_declaration_for_a_valid_company_passes(self):
        result = validate_eskd_export(
            Company(name="Moms AB", org_number="556677-8899"),
            date(2026, 1, 1),
            date(2026, 3, 31),
            self._complete_boxes(**{"49": Decimal("1250.00")}),
        )

        self.assertEqual(result["errors"], [])

    def test_paying_and_reclaiming_vat_in_the_same_period_is_rejected(self):
        """Box 49 (moms att betala) and box 50 (moms att få tillbaka) are the two
        outcomes of one period; both being positive means the calculation is wrong
        and the return would be invalid."""
        result = validate_eskd_export(
            Company(name="Moms AB", org_number="556677-8899"),
            date(2026, 1, 1),
            date(2026, 3, 31),
            self._complete_boxes(**{"49": Decimal("1250.00"), "50": Decimal("400.00")}),
        )

        self.assertTrue(
            any("49" in error and "50" in error for error in result["errors"]),
            f"expected a 49/50 conflict error, got {result['errors']}",
        )

    def test_only_one_of_box_49_and_50_being_set_is_fine(self):
        for code in ("49", "50"):
            with self.subTest(box=code):
                result = validate_eskd_export(
                    Company(name="Moms AB", org_number="556677-8899"),
                    date(2026, 1, 1),
                    date(2026, 3, 31),
                    self._complete_boxes(**{code: Decimal("999.00")}),
                )
                self.assertEqual(result["errors"], [])

    def test_a_missing_organisation_number_is_rejected(self):
        result = validate_eskd_export(
            Company(name="Moms AB", org_number=""),
            date(2026, 1, 1),
            date(2026, 3, 31),
            self._complete_boxes(),
        )

        self.assertTrue(
            any("rganisationsnummer" in error for error in result["errors"]),
            f"expected a missing org number error, got {result['errors']}",
        )

    def test_a_declaration_missing_skatteverket_fields_is_rejected_and_names_them(self):
        """A partial box dict means the calculation didn't produce every field
        Skatteverket expects; the error has to say which, or it's undiagnosable."""
        boxes = self._complete_boxes()
        del boxes["30"]
        del boxes["48"]

        result = validate_eskd_export(
            Company(name="Moms AB", org_number="556677-8899"),
            date(2026, 1, 1),
            date(2026, 3, 31),
            boxes,
        )

        missing_errors = [error for error in result["errors"] if "saknas" in error]
        self.assertEqual(len(missing_errors), 1, f"expected one missing-fields error, got {result['errors']}")
        self.assertIn("30", missing_errors[0])
        self.assertIn("48", missing_errors[0])
