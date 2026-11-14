from importlib import import_module

from django.apps import apps
from django.test import TestCase

from bookkeeping.models import Account, AccountClass
from saldovibe.testing import create_account, create_company

remap_vat_field_codes = import_module(
    "bookkeeping.migrations.0009_account_vat_field_codes_skv4700"
).remap_vat_field_codes


class VatFieldCodeMigrationTests(TestCase):
    """Existing charts were created from BAS data (and app labels) where section E was
    numbered one box off and import had no boxes; the data migration moves them."""

    def _account(self, number, code):
        account = create_account(self.company, number, number, AccountClass.REVENUE, vat_field_code=code)
        # save() auto-fills a blank code from BAS data; the migration must handle truly blank rows.
        Account.objects.filter(pk=account.pk).update(vat_field_code=code)
        return account

    def test_renumbers_section_e_and_fills_import_and_export_boxes_without_touching_custom_codes(self):
        self.company = create_company("Migrerad AB", "556100-0018")
        expectations = {
            self._account("3308", "38"): "39",  # appens gamla 38 = tjänster till EU
            self._account("3305", "39"): "40",  # appens gamla 39 = övriga tjänster utomlands
            self._account("3231", "39"): "41",  # byggtjänster: köparen betalningsskyldig i Sverige
            self._account("3004", "08"): "42",  # momsfri försäljning är inte hyra
            self._account("2615", "30"): "60",
            self._account("4545", ""): "50",
            self._account("3108", ""): "35",
            self._account("4070", ""): "20",
            self._account("4599", "20"): "20",  # eget konto, egen kod
            self._account("4546", "23"): "23",  # avviker från BAS-datan -> användarens val står
            self._account("4010", ""): "",  # inhemska inköp har ingen ruta
        }

        remap_vat_field_codes(apps, None)

        for account, expected in expectations.items():
            account.refresh_from_db()
            self.assertEqual(account.vat_field_code, expected, account.number)
