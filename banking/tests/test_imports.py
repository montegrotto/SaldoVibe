from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from banking.forms import parse_bank_csv
from banking.models import BankAccount, BankTransaction
from banking.services import ensure_tax_account_for_company
from banking.tests.base import BankingTestCase

# Riktiga exportformat per bank (otestade mot verkliga filer — byggda från bankernas
# dokumenterade CSV-rubriker). En rad per profil: (profil, csv, förväntat belopp, beskrivning).
UNTESTED_PROFILE_SAMPLES = [
    (
        "swedbank",
        "Radnr;Clearingnr;Kontonr;Produkt;Valuta;Bokföringsdag;Transaktionsdag;Valutadag;Referens;Beskrivning;Belopp;Bokfört saldo\n"
        "1;8327-9;123456789;Företagskonto;SEK;2026-06-21;2026-06-21;2026-06-21;Kortköp ICA;ICA KVANTUM;-123,45;9876,55\n",
        Decimal("-123.45"),
        "ICA KVANTUM",
    ),
    (
        "nordea",
        "Bokföringsdag;Belopp;Avsändare;Mottagare;Namn;Rubrik;Saldo;Valuta\n"
        "2026-06-21;-500,00;123456789;;Hyresvärden AB;Hyra juni;4500,00;SEK\n",
        Decimal("-500.00"),
        "Hyra juni",
    ),
    (
        "seb",
        "Bokföringsdatum;Valutadatum;Verifikationsnummer;Text/mottagare;Belopp;Saldo\n"
        "2026-06-21;2026-06-21;5501234567;Leverantör AB;-1000,00;8000,00\n",
        Decimal("-1000.00"),
        "Leverantör AB",
    ),
    (
        "danske_bank",
        "Bokföringsdatum;Specifikation;Belopp;Saldo\n2026-06-21;Swishbetalning;250,00;10250,00\n",
        Decimal("250.00"),
        "Swishbetalning",
    ),
    (
        "lansforsakringar",
        "Bokföringsdatum;Transaktionsdatum;Meddelande;Belopp;Saldo\n"
        "2026-06-21;2026-06-20;Autogiro försäkring;-350,00;7650,00\n",
        Decimal("-350.00"),
        "Autogiro försäkring",
    ),
    (
        "skandiabanken",
        "Bokföringsdag;Valutadag;Verifikationsnummer;Text;Belopp;Saldo\n"
        "2026-06-21;2026-06-21;987654;Löneutbetalning;25000,00;32650,00\n",
        Decimal("25000.00"),
        "Löneutbetalning",
    ),
    (
        "ica_banken",
        "Datum;Text;Typ;Budgetgrupp;Belopp;Saldo\n2026-06-21;ICA Nära;Korttransaktion;Mat;-89,50;2560,50\n",
        Decimal("-89.50"),
        "ICA Nära",
    ),
    (
        "avanza",
        "Datum;Konto;Typ av transaktion;Värdepapper/beskrivning;Antal;Kurs;Belopp;Courtage;Valuta;ISIN\n"
        "2026-06-21;Företagskonto;Insättning;Insättning juni;-;-;5000,00;-;SEK;-\n",
        Decimal("5000.00"),
        "Insättning juni",
    ),
    (
        "nordnet",
        "Id;Bokföringsdag;Affärsdag;Likviddag;Transaktionstyp;Belopp;Saldo;Transaktionstext;Verifikationsnummer\n"
        "1;2026-06-21;2026-06-21;2026-06-21;Insättning;3000,00;3000,00;Överföring från bank;700123\n",
        Decimal("3000.00"),
        "Överföring från bank",
    ),
    (
        "svea",
        "Bokföringsdatum;Text;Belopp;Saldo\n2026-06-21;Ränta;12,34;1012,34\n",
        Decimal("12.34"),
        "Ränta",
    ),
    (
        "resurs",
        "Bokföringsdatum;Text;Belopp;Saldo\n2026-06-21;Uttag;-200,00;800,00\n",
        Decimal("-200.00"),
        "Uttag",
    ),
    (
        "collector",
        "Bokföringsdatum;Text;Belopp;Saldo\n2026-06-21;Insättning;1500,00;2300,00\n",
        Decimal("1500.00"),
        "Insättning",
    ),
]


class UntestedProfileParseTests(BankingTestCase):
    def test_untested_profiles_parse_their_documented_export_format(self):
        for profile, csv_content, expected_amount, expected_description in UNTESTED_PROFILE_SAMPLES:
            with self.subTest(profile=profile):
                rows = parse_bank_csv(BytesIO(csv_content.encode("utf-8")), bank_profile=profile)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["amount"], expected_amount)
                self.assertEqual(rows[0]["description"], expected_description)


class BankImportTests(BankingTestCase):
    def test_import_creates_bank_transactions(self):
        csv_content = "date,description,amount,balance,external_id\n2026-06-20,Inbetalning,1500.00,10000.00,tx-1\n"
        upload = SimpleUploadedFile("bank.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": self.bank_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self._transaction_list_url_for_source())
        self.assertTrue(BankTransaction.objects.filter(company=self.company, external_id="tx-1").exists())

    def test_import_swedbank_profile_semicolon_format(self):
        self.bank_source.default_bank_profile = "swedbank"
        self.bank_source.save(update_fields=["default_bank_profile"])

        csv_content = "Bokföringsdag;Text;Belopp;Saldo;Referens\n2026-06-21;Kortköp; -123,45; 9876,55; swed-1\n"
        upload = SimpleUploadedFile("swedbank.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": self.bank_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self._transaction_list_url_for_source())
        tx = BankTransaction.objects.get(company=self.company, external_id="swed-1")
        self.assertEqual(str(tx.amount), "-123.45")

    def test_import_handelsbanken_company_account_export(self):
        self.bank_source.default_bank_profile = "handelsbanken"
        self.bank_source.save(update_fields=["default_bank_profile"])

        csv_content = (
            "sep=;\n"
            "Kontohavare;Kontonr;IBAN;BIC;Kontoform;Valuta;Kontoförande kontor;Datum intervall;Kontor;Bokföringsdag;Reskontradag;Valutadag;Referens;Insättning/Uttag;Bokfört saldo;Aktuellt saldo;Valutadagssaldo;Referens Swish;Avsändar-id Swish;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;;;;;;;;666,66;666,66;;;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;6885;2026-08-04;2026-08-04;2026-08-04;EON Kundsuppor;-680,00;666,66;;;;;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;6885;2026-08-04;2026-08-04;2026-08-04;EON Kundsuppor;-1774,00;1346,66;;;;;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;6885;2026-08-03;2026-08-03;2026-08-04;RESURS;3000,00;3120,66;;;;;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;6885;2026-08-03;2026-08-03;2026-08-03;EON Kundsuppor;-1996,00;120,66;;;;;\n"
            "SKÖLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-08-03 - 2026-08-13;6885;2026-08-03;2026-08-03;2026-08-03;EON Kundsuppor;-1996,00;2116,66;;;;;\n"
        )
        upload = SimpleUploadedFile("handelsbanken.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": self.bank_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        imported = BankTransaction.objects.filter(company=self.company, bank_account=self.bank_source)
        # Summary-raden (utan bokföringsdag/belopp) hoppas över; de två identiska
        # EON-raderna på -1996,00 måste båda importeras, inte dedupliceras bort.
        self.assertEqual(imported.count(), 5)
        self.assertEqual(imported.filter(description="EON Kundsuppor", amount=Decimal("-1996.00")).count(), 2)
        resurs = imported.get(description="RESURS")
        self.assertEqual(resurs.amount, Decimal("3000.00"))
        self.assertEqual(resurs.balance, Decimal("3120.66"))

    def test_import_skattekonto_transactions_with_skatteverket_profile(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")

        csv_content = (
            "Bokföringsdatum;Transaktion;Belopp;Saldo;HändelseID\n"
            "2026-07-01;Debiterad preliminärskatt;-3500,00;12500,00;skat-1\n"
        )
        upload = SimpleUploadedFile("skattekonto.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": tax_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('banking:transaction_list')}?bank_account={tax_source.pk}")
        tx = BankTransaction.objects.get(company=self.company, external_id="skat-1")
        self.assertEqual(str(tx.amount), "-3500.00")

    def test_import_skattekonto_transactions_handles_sep_and_summary_row(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")

        csv_content = (
            "sep=;\n"
            "Kontohavare;Kontonr;IBAN;BIC;Kontoform;Valuta;Kontoförande kontor;Datum intervall;Kontor;Bokföringsdag;Reskontradag;Valutadag;Referens;Insättning/Uttag;Bokfört saldo;Aktuellt saldo;Valutadagssaldo;Referens Swish;Avsändar-id Swish;\n"
            "SKÄLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-06-04 - 2026-06-15;;;;;;;;11337,66;2677,46;;;\n"
            "SKÄLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-06-04 - 2026-06-15;6091;2026-06-15;2026-06-15;2026-06-16;5469-9814 00027;10487,00;11337,66;;;;;\n"
            "SKÄLLERBO AB;595385281;SE7060000000000595385281;HANDSESS;Affärskonto;SEK;6582 Kumla;2026-06-04 - 2026-06-15;6091;2026-06-15;2026-06-15;2026-06-15;HB KORT;-1826,80;850,66;;;;;\n"
        )
        upload = SimpleUploadedFile(
            "skattekonto-handelsbanken.csv", csv_content.encode("utf-8"), content_type="text/csv"
        )

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": tax_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('banking:transaction_list')}?bank_account={tax_source.pk}")
        imported = BankTransaction.objects.filter(company=self.company, bank_account=tax_source)
        self.assertEqual(imported.count(), 2)
        self.assertTrue(imported.filter(external_id="5469-9814 00027").exists())
        hb_card = imported.get(external_id="HB KORT")
        self.assertEqual(str(hb_card.amount), "-1826.80")

    def test_import_skattekonto_transactions_without_header_row(self):
        ensure_tax_account_for_company(self.company)
        tax_source = BankAccount.objects.get(company=self.company, account_type="tax")

        csv_content = (
            '"Damadev AB";"559123-3878";"";""\n'
            '"";"";"";""\n'
            '"";"Ingående saldo 2024-01-04";"";"174 481"\n'
            '"2024-01-06";"Intäktsränta";"341";"174 822"\n'
            '"2024-01-16";"Inbetalning bokförd 240115";"100 000";"274 822"\n'
            '"";"Utgående saldo 2026-07-07";"";"60 106"\n'
        )
        upload = SimpleUploadedFile("skattekonto-no-header.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(
            reverse("banking:import_transactions"),
            {
                "bank_account": tax_source.pk,
                "csv_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        imported = BankTransaction.objects.filter(company=self.company, bank_account=tax_source)
        self.assertEqual(imported.count(), 2)
        first = imported.filter(description="Intäktsränta").first()
        self.assertIsNotNone(first)
        self.assertEqual(first.amount, Decimal("341.00"))
