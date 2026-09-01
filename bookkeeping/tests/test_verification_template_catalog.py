from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from bookkeeping.bas_accounts import lookup_bas_account, seed_bas_2026_accounts_for_company
from bookkeeping.models import (
    AccountClass,
    Transaction,
    VerificationTemplate,
    VerificationTemplateEntry,
)
from bookkeeping.verification_template_catalog import (
    compute_template_amounts,
    entry_rules_from_model,
    import_catalog_templates_for_company,
    load_template_catalog,
)
from saldovibe.testing import (
    CompanyTestCase,
    create_account,
    create_accounting_year,
    create_company,
    create_user,
    set_active_company,
)


class TemplateCatalogDataTests(TestCase):
    def test_catalog_loads_and_every_account_exists_in_bas(self):
        catalog = load_template_catalog()

        self.assertGreater(len(catalog), 0)
        for template in catalog:
            for entry in template["entries"]:
                self.assertIsNotNone(
                    lookup_bas_account(entry["account"]),
                    f"{template['slug']}: kontot {entry['account']} saknas i BAS 2026",
                )

    def test_every_fully_ruled_template_balances(self):
        """Öresavrundningen ska absorberas av remainder-raden, även på ojämna basbelopp."""
        for template in load_template_catalog():
            rules = [entry["amount_rule"] for entry in template["entries"]]
            if VerificationTemplateEntry.AmountRule.NONE in rules:
                continue

            for base in (Decimal("10000.00"), Decimal("3333.33"), Decimal("1.07")):
                with self.subTest(slug=template["slug"], base=base):
                    amounts = compute_template_amounts(template["entries"], base)
                    debit = sum(
                        (a for e, a in zip(template["entries"], amounts) if e["side"] == "debit"),
                        Decimal("0.00"),
                    )
                    credit = sum(
                        (a for e, a in zip(template["entries"], amounts) if e["side"] == "credit"),
                        Decimal("0.00"),
                    )
                    self.assertEqual(debit, credit)


class ComputeTemplateAmountsTests(TestCase):
    def test_percent_and_remainder(self):
        entries = [
            {"side": "debit", "amount_rule": "percent", "amount_percent": Decimal("80")},
            {"side": "debit", "amount_rule": "percent", "amount_percent": Decimal("20")},
            {"side": "credit", "amount_rule": "remainder", "amount_percent": None},
        ]

        self.assertEqual(
            compute_template_amounts(entries, Decimal("12500.00")),
            [Decimal("10000.00"), Decimal("2500.00"), Decimal("12500.00")],
        )

    def test_remainder_absorbs_rounding(self):
        entries = [
            {"side": "debit", "amount_rule": "percent", "amount_percent": Decimal("80")},
            {"side": "debit", "amount_rule": "percent", "amount_percent": Decimal("20")},
            {"side": "credit", "amount_rule": "remainder", "amount_percent": None},
        ]

        amounts = compute_template_amounts(entries, Decimal("1.07"))

        self.assertEqual(amounts[0] + amounts[1], amounts[2])

    def test_manual_rows_are_left_empty(self):
        entries = [
            {"side": "debit", "amount_rule": "none", "amount_percent": None},
            {"side": "credit", "amount_rule": "none", "amount_percent": None},
        ]

        self.assertEqual(compute_template_amounts(entries, Decimal("100.00")), [None, None])


class CatalogImportTests(CompanyTestCase):
    user_email = "catalog@example.com"
    company_name = "Katalogbolag AB"
    company_org_number = "556677-3344"
    accounting_year_dates = None
    # The catalog maps templates onto BAS account numbers, so the whole chart has to exist.
    seed_bas_accounts = True

    def setUp(self):
        super().setUp()
        self.slug = load_template_catalog()[0]["slug"]

    def test_import_creates_template_with_entries(self):
        created, updated, skipped = import_catalog_templates_for_company(self.company, [self.slug])

        self.assertEqual((created, updated, skipped), (1, 0, []))
        template = VerificationTemplate.objects.get(company=self.company, slug=self.slug)
        self.assertGreaterEqual(template.entries.count(), 2)

    def test_reimport_updates_instead_of_duplicating(self):
        import_catalog_templates_for_company(self.company, [self.slug])
        created, updated, skipped = import_catalog_templates_for_company(self.company, [self.slug])

        self.assertEqual((created, updated, skipped), (0, 1, []))
        self.assertEqual(VerificationTemplate.objects.filter(company=self.company, slug=self.slug).count(), 1)

    def test_missing_accounts_are_skipped_not_partially_imported(self):
        # Deliberately *not* BAS-seeded: one lone account is what makes the import skip.
        bare_company = create_company("Tomt AB", "556677-5566")
        create_account(bare_company, "1930", "Företagskonto", AccountClass.ASSET)

        created, updated, skipped = import_catalog_templates_for_company(bare_company, [self.slug])

        self.assertEqual((created, updated), (0, 0))
        self.assertEqual(len(skipped), 1)
        self.assertFalse(VerificationTemplate.objects.filter(company=bare_company).exists())

    def test_unknown_slug_is_reported(self):
        created, updated, skipped = import_catalog_templates_for_company(self.company, ["finns-inte"])

        self.assertEqual((created, updated), (0, 0))
        self.assertEqual(skipped[0][0], "finns-inte")


class TemplateLibraryViewTests(CompanyTestCase):
    user_email = "library@example.com"
    company_name = "Biblioteksbolag AB"
    company_org_number = "556677-7788"
    accounting_year_dates = None
    seed_bas_accounts = True

    def setUp(self):
        super().setUp()
        self.slug = load_template_catalog()[0]["slug"]

    def test_library_lists_catalog(self):
        response = self.client.get(reverse("bookkeeping:verification_template_library"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["categories"])

    def test_post_imports_selected_templates(self):
        response = self.client.post(
            reverse("bookkeeping:verification_template_library"),
            {"slugs": [self.slug]},
        )

        self.assertRedirects(response, reverse("bookkeeping:verification_template_list"))
        self.assertTrue(VerificationTemplate.objects.filter(company=self.company, slug=self.slug).exists())

    def test_imported_template_produces_a_balanced_voucher(self):
        """Beloppen som mallreglerna räknar fram ska gå att spara rakt av."""
        create_accounting_year(self.company)
        import_catalog_templates_for_company(self.company, [self.slug])
        template = VerificationTemplate.objects.get(company=self.company, slug=self.slug)

        entries = list(template.entries.all())
        amounts = compute_template_amounts(entry_rules_from_model(entries), Decimal("12500.00"))
        if any(amount is None for amount in amounts):
            self.skipTest("Den första katalogmallen har manuella rader.")

        payload = {
            "date": "2026-06-01",
            "description": template.name,
            "entries-TOTAL_FORMS": str(len(entries)),
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "2",
            "entries-MAX_NUM_FORMS": "1000",
        }
        for index, (entry, amount) in enumerate(zip(entries, amounts)):
            payload[f"entries-{index}-account"] = str(entry.account_id)
            payload[f"entries-{index}-debit"] = str(amount) if entry.is_debit else "0.00"
            payload[f"entries-{index}-credit"] = str(amount) if entry.is_credit else "0.00"

        response = self.client.post(reverse("bookkeeping:transaction_add"), payload)

        self.assertRedirects(response, reverse("bookkeeping:transaction_list"))
        txn = Transaction.objects.get(description=template.name)
        self.assertTrue(txn.is_balanced)

    def test_import_is_scoped_to_active_company(self):
        """En användare utan tillgång till företaget ska inte kunna importera in i det."""
        other_user = create_user("outsider@example.com")
        other_company = create_company("Annat AB", "556677-9900", users=[other_user])
        seed_bas_2026_accounts_for_company(other_company)

        self.client.force_login(other_user)
        # Point the outsider's session at a company they are not a member of.
        set_active_company(self.client, self.company)

        self.client.post(reverse("bookkeeping:verification_template_library"), {"slugs": [self.slug]})

        self.assertFalse(VerificationTemplate.objects.filter(company=self.company).exists())
        self.assertTrue(VerificationTemplate.objects.filter(company=other_company, slug=self.slug).exists())
