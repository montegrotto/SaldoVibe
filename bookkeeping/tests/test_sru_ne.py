"""NE-bilagan (SRU för enskild firma): kontokoppling, teckenkonvention och exportfil."""

import io
import zipfile
from decimal import Decimal

from django.urls import reverse

from bookkeeping.models import AccountClass, Company, JournalEntry, Transaction
from bookkeeping.sru_ne import resolve_ne_code
from saldovibe.testing import CompanyTestCase, create_account


class ResolveNeCodeTests(CompanyTestCase):
    def test_mapping_samples_from_bas_table(self):
        self.assertEqual(resolve_ne_code("1930"), "7280")
        self.assertEqual(resolve_ne_code("1291"), "7211")
        self.assertEqual(resolve_ne_code("2440"), "7382")
        self.assertEqual(resolve_ne_code("2650"), "7383")
        self.assertEqual(resolve_ne_code("3001", vat_field_code="05"), "7400")
        self.assertEqual(resolve_ne_code("3001", vat_field_code=""), "7401")
        self.assertEqual(resolve_ne_code("7010"), "7502")
        self.assertEqual(resolve_ne_code("7832"), "7505")
        self.assertEqual(resolve_ne_code("8423"), "7503")
        # Kopplingstabellens (+)/(-)-konton följer saldots tecken.
        self.assertEqual(resolve_ne_code("8890", credit_net=Decimal("100")), "7403")
        self.assertEqual(resolve_ne_code("8890", credit_net=Decimal("-100")), "7503")
        self.assertEqual(resolve_ne_code("2510"), "")
        self.assertEqual(resolve_ne_code("8999"), "")


class NeExportTests(CompanyTestCase):
    user_email = "ne-user@example.com"
    user_fields = {"is_staff": True}
    company_name = "Firma Nilsson"
    company_org_number = "19800101-1234"
    company_fields = {"legal_form": Company.LegalForm.ENSKILD_FIRMA}

    def setUp(self):
        super().setUp()
        self.bank = create_account(self.company, "1930", "Företagskonto", AccountClass.ASSET)
        self.sales = create_account(self.company, "3001", "Försäljning", AccountClass.REVENUE, vat_field_code="05")
        self.rent = create_account(self.company, "5010", "Lokalhyra", AccountClass.OTHER_EXTERNAL)
        self.payable = create_account(self.company, "2440", "Leverantörsskulder", AccountClass.EQUITY_LIABILITY)
        txn = Transaction.objects.create(
            accounting_year=self.year, date="2026-06-01", description="NE", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=self.bank, debit=Decimal("1000.00"))
        JournalEntry.objects.create(transaction=txn, account=self.sales, credit=Decimal("1000.00"))
        JournalEntry.objects.create(transaction=txn, account=self.rent, debit=Decimal("400.00"))
        JournalEntry.objects.create(transaction=txn, account=self.payable, credit=Decimal("400.00"))

    def test_report_groups_by_ne_field_with_form_signs(self):
        response = self.client.get(f"{reverse('bookkeeping:sru_report')}?year={self.year.pk}")
        self.assertEqual(response.status_code, 200)
        rows = dict(response.context["sru_rows"])
        self.assertEqual(rows["7280"]["net"], Decimal("1000.00"))  # B9 tillgång: debet
        self.assertEqual(rows["7382"]["net"], Decimal("400.00"))  # B15 skuld: kredit, positivt
        self.assertEqual(rows["7400"]["net"], Decimal("1000.00"))  # R1 intäkt
        self.assertEqual(rows["7501"]["net"], Decimal("400.00"))  # R6 kostnad, positivt
        self.assertEqual(rows["7440"]["net"], Decimal("600.00"))  # R11 = R1 - R6
        self.assertContains(response, "NE-bilagan")
        self.assertContains(response, "#BLANKETT NE-2026P4")

    def test_download_writes_ne_block_with_personnummer(self):
        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")
        self.assertEqual(response.status_code, 200)
        blankett = zipfile.ZipFile(io.BytesIO(response.content)).read("blanketter.sru").decode("cp437")
        self.assertIn("#BLANKETT NE-2026P4\r\n#IDENTITET 198001011234 20261231", blankett)
        self.assertIn("#UPPGIFT 7011 20260101", blankett)
        self.assertIn("#UPPGIFT 7023 X", blankett)
        self.assertIn("#UPPGIFT 7382 400", blankett)
        self.assertIn("#UPPGIFT 7440 600", blankett)
        self.assertNotIn("7201", blankett)  # ingen INK2-kod läcker in

    def test_unmapped_account_blocks_export_but_skatteskuld_is_ignored(self):
        tax = create_account(self.company, "2510", "Skatteskulder", AccountClass.EQUITY_LIABILITY)
        odd = create_account(self.company, "2081", "Aktiekapital", AccountClass.EQUITY_LIABILITY)
        txn = Transaction.objects.create(
            accounting_year=self.year, date="2026-07-01", description="x", created_by=self.user
        )
        JournalEntry.objects.create(transaction=txn, account=tax, debit=Decimal("10.00"))
        JournalEntry.objects.create(transaction=txn, account=odd, credit=Decimal("10.00"))

        response = self.client.get(f"{reverse('bookkeeping:sru_report')}?year={self.year.pk}")
        missing = [a.number for a in response.context["preflight"]["missing_code_accounts"]]
        self.assertEqual(missing, ["2081"])
        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")
        self.assertEqual(response.status_code, 302)

    def test_download_requires_twelve_digit_personnummer(self):
        self.company.org_number = "556677-8899"
        self.company.save(update_fields=["org_number"])
        response = self.client.get(f"{reverse('bookkeeping:sru_download')}?year={self.year.pk}")
        self.assertEqual(response.status_code, 302)
