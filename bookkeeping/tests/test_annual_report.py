"""Årsredovisning K2: kontomappningens täckning, uppställningarna ur huvudboken och vyerna."""

import json
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse

from bookkeeping.annual_report import (
    BALANCE_SHEET,
    INCOME_STATEMENT,
    NOT_FOR_AB_RANGE,
    RESULT_TRANSFER_RANGE,
    build_annual_report_context,
    iter_posts,
)
from bookkeeping.bas_accounts import _DATA_FILE
from bookkeeping.models import AccountClass, AnnualReport, Company, JournalEntry, Transaction, TransactionSource
from bookkeeping.year_end import create_year_end_vouchers
from saldovibe.testing import CompanyTestCase, create_accounting_year, create_accounts

ACCOUNT_SPECS = [
    ("1220", "Inventarier och verktyg", AccountClass.ASSET),
    ("1229", "Ackumulerade avskrivningar på inventarier", AccountClass.ASSET),
    ("1930", "Företagskonto", AccountClass.ASSET),
    ("2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY),
    ("2091", "Balanserad vinst eller förlust", AccountClass.EQUITY_LIABILITY),
    ("2099", "Årets resultat", AccountClass.EQUITY_LIABILITY),
    ("2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY),
    ("3001", "Försäljning", AccountClass.REVENUE),
    ("5010", "Lokalhyra", AccountClass.OTHER_EXTERNAL),
    ("7832", "Avskrivningar på inventarier", AccountClass.PERSONNEL),
    ("8999", "Årets resultat", AccountClass.FINANCIAL),
]


def _rows_by_label(rows):
    return {row["label"]: row for row in rows}


class MappingTests(SimpleTestCase):
    def test_every_bas_account_maps_to_exactly_one_post_and_posts_never_overlap(self):
        ranges = [
            (lo, hi, post.label) for post in iter_posts(INCOME_STATEMENT + BALANCE_SHEET) for lo, hi in post.ranges
        ]
        for number in range(1000, 9000):
            hits = [label for lo, hi, label in ranges if lo <= number <= hi]
            self.assertLessEqual(len(hits), 1, f"{number} ligger i flera poster: {hits}")

        skipped = (NOT_FOR_AB_RANGE, RESULT_TRANSFER_RANGE)
        for account in json.loads(_DATA_FILE.read_text(encoding="utf-8")):
            number = int(account["number"])
            if any(lo <= number <= hi for lo, hi in skipped):
                continue
            hits = [label for lo, hi, label in ranges if lo <= number <= hi]
            self.assertEqual(len(hits), 1, f"{number} {account['name']} saknar post")


class AnnualReportTestCase(CompanyTestCase):
    user_fields = {"is_staff": True}
    company_fields = {"legal_form": Company.LegalForm.AKTIEBOLAG, "city": "Umeå"}
    accounting_year_dates = ("2025-01-01", "2025-12-31")

    def setUp(self):
        super().setUp()
        self.prev_year = self.year
        self.prev_year.refresh_from_db()
        self.year = create_accounting_year(self.company, "2026-01-01", "2026-12-31")
        self.year.refresh_from_db()  # date-fält som date, inte str
        self.accounts = create_accounts(self.company, ACCOUNT_SPECS)

        # 2025: aktiekapital 25 000, försäljning 50 000 → resultat 50 000, avslutat (S1 + S2).
        self.book(self.prev_year, date(2025, 1, 10), "1930", "2081", 25_000)
        self.book(self.prev_year, date(2025, 6, 1), "1930", "3001", 50_000)
        create_year_end_vouchers(self.company, self.user, self.prev_year, self.year)

        # 2026: försäljning 100 000, hyra 20 000 (obetald), inventarier 12 000, avskrivning 2 400.
        self.book(self.year, date(2026, 3, 1), "1930", "3001", 100_000)
        self.book(self.year, date(2026, 4, 1), "5010", "2440", 20_000)
        self.book(self.year, date(2026, 5, 1), "1220", "1930", 12_000)
        self.book(self.year, date(2026, 12, 31), "7832", "1229", 2_400)

    def book(self, year, day, debit_number, credit_number, amount):
        txn = Transaction.objects.create(
            accounting_year=year, date=day, description="Test", source=TransactionSource.MANUAL, created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=self.accounts[debit_number], debit=Decimal(amount))
        JournalEntry.objects.create(transaction=txn, account=self.accounts[credit_number], credit=Decimal(amount))
        return txn

    def context(self, **report_fields):
        report = AnnualReport(accounting_year=self.year, **report_fields)
        return build_annual_report_context(self.company, self.year, report)

    def test_statements_before_and_after_closing_the_year(self):
        for closed in (False, True):
            if closed:
                create_accounting_year(self.company, "2027-01-01", "2027-12-31")
                create_year_end_vouchers(
                    self.company, self.user, self.year, self.company.accounting_years.get(end_date="2027-12-31")
                )
            with self.subTest(closed=closed):
                context = self.context()
                self.assertEqual(context["is_closed"], closed)
                rr = _rows_by_label(context["rr_rows"])
                self.assertEqual((rr["Nettoomsättning"]["cur"], rr["Nettoomsättning"]["prev"]), (100_000, 50_000))
                self.assertEqual(rr["Övriga externa kostnader"]["cur"], -20_000)
                self.assertEqual(
                    rr["Av- och nedskrivningar av materiella och immateriella anläggningstillgångar"]["cur"], -2_400
                )
                self.assertEqual((rr["Årets resultat"]["cur"], rr["Årets resultat"]["prev"]), (77_600, 50_000))
                self.assertNotIn("Bokslutsdispositioner", rr)  # tom grupp utelämnas

                br = _rows_by_label(context["br_rows"])
                self.assertEqual(br["Inventarier, verktyg och installationer"]["cur"], 9_600)
                self.assertEqual((br["Kassa och bank"]["cur"], br["Kassa och bank"]["prev"]), (163_000, 75_000))
                self.assertEqual((br["Summa tillgångar"]["cur"], br["Summa tillgångar"]["prev"]), (172_600, 75_000))
                self.assertEqual(br["Aktiekapital"]["cur"], 25_000)
                self.assertEqual((br["Balanserat resultat"]["cur"], br["Balanserat resultat"]["prev"]), (50_000, 0))
                self.assertEqual((br["Årets resultat"]["cur"], br["Årets resultat"]["prev"]), (77_600, 50_000))
                self.assertEqual(br["Leverantörsskulder"]["cur"], 20_000)
                self.assertEqual(br["Summa eget kapital och skulder"]["cur"], 172_600)
                self.assertEqual(br["Summa eget kapital och skulder"]["prev"], 75_000)

    def test_rounding_difference_lands_on_retained_earnings(self):
        # Per post avrundat: 101 000,50 → 101 001 men 20 200,25 → 20 200, så sidorna skiljer 1 kr
        # - differensen läggs på kassaraden.
        self.book(self.year, date(2026, 7, 1), "1930", "3001", "1000.50")
        self.book(self.year, date(2026, 7, 2), "5010", "1930", "200.25")
        br = _rows_by_label(self.context()["br_rows"])
        self.assertEqual(br["Årets resultat"]["cur"], 78_401)
        self.assertEqual(br["Balanserat resultat"]["cur"], 50_000)
        self.assertEqual(br["Summa eget kapital och skulder"]["cur"], 173_401)
        self.assertEqual(br["Kassa och bank"]["cur"], 163_801)  # 163 800,25 + 1 kr avrundning
        self.assertEqual(br["Summa kassa och bank"]["cur"], 163_801)
        self.assertEqual(br["Summa tillgångar"]["cur"], 173_401)

    def test_management_report_tables_and_notes(self):
        context = self.context(dividend=Decimal("10000.00"), signatories="Anna Andersson, Styrelseledamot\nBo Berg, VD")

        self.assertEqual([c["year"].name for c in context["multi_year"]], ["2026", "2025"])
        self.assertEqual(context["multi_year"][0]["net_sales"], 100_000)
        self.assertEqual(context["multi_year"][0]["result_after_financial"], 77_600)
        self.assertEqual(context["multi_year"][0]["solidity"], Decimal("88.4"))

        rows = dict(context["equity_changes"]["rows"])
        self.assertEqual(rows["Belopp vid årets ingång"], [25_000, 0, 0, 50_000, 75_000])
        self.assertEqual(rows["Disposition enligt årsstämmans beslut"], [0, 0, 50_000, -50_000, 0])
        self.assertEqual(rows["Årets resultat"], [0, 0, 0, 77_600, 77_600])
        self.assertEqual(rows["Belopp vid årets utgång"], [25_000, 0, 50_000, 77_600, 152_600])

        self.assertEqual(
            context["disposition"],
            {"retained": 50_000, "result": 77_600, "total": 127_600, "dividend": 10_000, "carried_forward": 117_600},
        )

        note = next(n for n in context["notes"] if n["kind"] == "fixed_asset")
        self.assertEqual(note["title"], "Inventarier, verktyg och installationer")
        self.assertEqual(note["cur"]["purchases"], 12_000)
        self.assertEqual(note["cur"]["dep_year"], -2_400)
        self.assertEqual(note["cur"]["book_value"], 9_600)
        br = _rows_by_label(context["br_rows"])
        self.assertEqual(br["Inventarier, verktyg och installationer"]["note"], note["number"])

        self.assertTrue(context["has_ceo"])
        self.assertEqual(context["signatories"][1], {"name": "Bo Berg", "role": "VD"})
        self.assertIn("inte avslutat", " ".join(context["warnings"]))

    def test_page_saves_texts_and_pdf_downloads(self):
        url = reverse("bookkeeping:annual_report", kwargs={"pk": self.year.pk})
        response = self.client.get(url)
        self.assertContains(response, "Nettoomsättning")
        self.assertContains(response, "Företaget har sitt säte i Umeå.")

        response = self.client.post(
            url,
            {
                "business_description": "Konsultverksamhet inom IT.",
                "significant_events": "",
                "dividend": "0",
                "pledged_assets": "Inga",
                "contingent_liabilities": "Inga",
                "signing_place": "Umeå",
                "signing_date": "2027-03-15",
                "adoption_date": "2027-03-14",
                "signatories": "Anna Andersson, Styrelseledamot",
                "auditor_text": "",
                "certifier": "",
            },
        )
        self.assertRedirects(response, url)
        self.assertEqual(self.year.annual_report.business_description, "Konsultverksamhet inom IT.")
        self.assertContains(self.client.get(url), "fastställdes av styrelsen den")

        response = self.client.get(reverse("bookkeeping:annual_report_pdf", kwargs={"pk": self.year.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_only_aktiebolag_get_an_annual_report(self):
        self.company.legal_form = Company.LegalForm.ENSKILD_FIRMA
        self.company.save()
        response = self.client.get(reverse("bookkeeping:annual_report", kwargs={"pk": self.year.pk}), follow=True)
        self.assertRedirects(response, reverse("bookkeeping:accounting_year_list"))
        self.assertContains(response, "finns bara för aktiebolag")
