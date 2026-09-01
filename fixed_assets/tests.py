from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from bookkeeping.models import Account, Transaction
from saldovibe.testing import (
    CompanyTestCase,
    create_account,
    create_accounting_year,
    create_accounts,
    create_company,
)

from .models import (
    FixedAsset,
    FixedAssetDepreciation,
    FixedAssetType,
    ensure_default_asset_types,
)


class FixedAssetModelTests(TestCase):
    def setUp(self):
        self.company = create_company("Testbolaget AB")
        # Spans two years: the depreciation schedules below run past 2026.
        self.accounting_year = create_accounting_year(self.company, date(2026, 1, 1), date(2027, 12, 31))
        accounts = create_accounts(
            self.company,
            [
                ("7830", "Avskrivningar", "7"),
                ("1229", "Ack avskrivningar inventarier", "1"),
                ("7732", "Nedskrivningar inventarier", "7"),
                ("1228", "Ack nedskrivningar inventarier", "1"),
                ("1220", "Inventarier", "1"),
                ("3973", "Vinst vid avyttring", "3"),
                ("7973", "Förlust vid avyttring", "7"),
            ],
        )
        self.expense_account = accounts["7830"]
        self.accumulated_account = accounts["1229"]
        self.impairment_expense_account = accounts["7732"]
        self.accumulated_impairment_account = accounts["1228"]
        ensure_default_asset_types(self.company)

    def test_depreciation_is_blocked_in_a_locked_period(self):
        from bookkeeping.models import PeriodLock

        asset = FixedAsset.objects.create(
            company=self.company,
            name="Kontorsdator",
            asset_type=FixedAsset.AssetType.COMPUTER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        PeriodLock.objects.create(
            company=self.company,
            accounting_year=self.accounting_year,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            is_locked=True,
            reason="Bokslut februari",
        )

        with self.assertRaisesMessage(ValidationError, "låst"):
            asset.register_monthly_depreciation()
        self.assertEqual(Transaction.objects.count(), 0)

    def test_register_monthly_depreciation_and_complete(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Kontorsdator",
            asset_type=FixedAsset.AssetType.COMPUTER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        self.assertEqual(asset.monthly_depreciation_amount, Decimal("1000.00"))
        self.assertEqual(str(asset.next_depreciation_date), "2026-02-01")

        first_dep = asset.register_monthly_depreciation()
        self.assertEqual(first_dep.amount, Decimal("1000.00"))

        for _ in range(11):
            asset.register_monthly_depreciation()

        asset.refresh_from_db()
        self.assertTrue(asset.is_fully_depreciated)
        self.assertEqual(asset.current_book_value, Decimal("0.00"))

    def test_last_month_adjusts_rounding_difference(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Maskin",
            asset_type=FixedAsset.AssetType.MACHINERY,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 1, 1),
            acquisition_value=Decimal("1000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=3,
        )

        dep1 = asset.register_monthly_depreciation()
        dep2 = asset.register_monthly_depreciation()
        dep3 = asset.register_monthly_depreciation()

        self.assertEqual(dep1.amount, Decimal("333.33"))
        self.assertEqual(dep2.amount, Decimal("333.33"))
        self.assertEqual(dep3.amount, Decimal("333.34"))
        self.assertEqual(asset.total_depreciated, Decimal("1000.00"))

    def test_salvage_can_equal_acquisition_before_depreciation(self):
        asset = FixedAsset(
            company=self.company,
            name="Leasad enhet",
            asset_type=FixedAsset.AssetType.OTHER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("10000.00"),
            salvage_value=Decimal("10000.00"),
            useful_life_months=12,
        )

        asset.full_clean()

    def test_salvage_cannot_equal_acquisition_after_depreciation_started(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Kontorsdator 2",
            asset_type=FixedAsset.AssetType.COMPUTER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        asset.register_monthly_depreciation()
        asset.salvage_value = asset.acquisition_value

        with self.assertRaises(ValidationError):
            asset.full_clean()

    def test_equal_salvage_and_acquisition_is_not_due_for_depreciation(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Leasad enhet 2",
            asset_type=FixedAsset.AssetType.OTHER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("10000.00"),
            salvage_value=Decimal("10000.00"),
            useful_life_months=12,
        )

        self.assertTrue(asset.is_fully_depreciated)
        self.assertFalse(asset.is_due_for_depreciation)
        self.assertIsNone(asset.next_depreciation_date)

    def test_total_depreciated_marks_asset_fully_depreciated(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Maskin B",
            asset_type=FixedAsset.AssetType.MACHINERY,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("1000.00"),
            salvage_value=Decimal("100.00"),
            useful_life_months=12,
        )

        asset.register_monthly_depreciation()
        asset.salvage_value = Decimal("925.00")

        self.assertTrue(asset.is_fully_depreciated)
        self.assertFalse(asset.is_due_for_depreciation)

    def test_register_depreciation_creates_balanced_transaction(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Inventarie",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        dep = asset.register_monthly_depreciation()

        self.assertIsNotNone(dep.transaction)
        self.assertEqual(dep.transaction.total_debit, dep.amount)
        self.assertEqual(dep.transaction.total_credit, dep.amount)
        entry_accounts = {entry.account.number for entry in dep.transaction.entries.all()}
        self.assertEqual(entry_accounts, {"7830", "1229"})

    def test_register_depreciation_fails_when_required_accounts_missing(self):
        FixedAssetType.objects.create(
            company=self.company,
            key="broken_type",
            name="Trasig typ",
            depreciation_expense_account=None,
            accumulated_depreciation_account=None,
            is_active=True,
            sort_order=999,
        )

        asset = FixedAsset.objects.create(
            company=self.company,
            name="Inventarie utan konton",
            asset_type="broken_type",
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        with self.assertRaises(ValidationError):
            asset.register_monthly_depreciation()

    def test_register_impairment_reduces_book_value_and_posts_balanced_transaction(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Skadad maskin",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        impairment = asset.register_impairment(
            period=date(2026, 3, 1), amount=Decimal("2000.00"), reason="Skadad vid transport"
        )

        self.assertEqual(impairment.transaction.total_debit, Decimal("2000.00"))
        self.assertEqual(impairment.transaction.total_credit, Decimal("2000.00"))
        entry_accounts = {entry.account.number for entry in impairment.transaction.entries.all()}
        self.assertEqual(entry_accounts, {"7732", "1228"})

        asset.refresh_from_db()
        self.assertEqual(asset.total_impaired, Decimal("2000.00"))
        self.assertEqual(asset.current_book_value, Decimal("10000.00"))

    def test_register_impairment_requires_reason(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Utan anledning",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        with self.assertRaises(ValidationError):
            asset.register_impairment(period=date(2026, 3, 1), amount=Decimal("2000.00"), reason="")

    def test_register_impairment_cannot_exceed_book_value(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Övernedskriven",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        with self.assertRaises(ValidationError):
            asset.register_impairment(period=date(2026, 3, 1), amount=Decimal("20000.00"), reason="För mycket")

    def test_register_depreciation_correction_adjusts_total_without_double_counting_schedule(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Korrigerad",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        original = asset.register_monthly_depreciation()

        correction = asset.register_depreciation_correction(
            original, Decimal("-100.00"), reason="Felaktig period bokförd"
        )

        self.assertTrue(correction.is_correction)
        self.assertEqual(correction.correction_of, original)
        correction.transaction.validate_balanced()

        asset.refresh_from_db()
        self.assertEqual(asset.depreciation_count, 1)
        self.assertEqual(asset.total_depreciated, Decimal("900.00"))

    def test_register_depreciation_correction_rejects_zero_amount(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Nollkorrigering",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        original = asset.register_monthly_depreciation()

        with self.assertRaises(ValidationError):
            asset.register_depreciation_correction(original, Decimal("0.00"), reason="Ogiltig")

    def test_dispose_blocks_further_depreciation_and_double_disposal(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Utrangerad",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        asset.register_monthly_depreciation()

        asset.dispose(
            disposal_date=date(2026, 3, 1),
            disposal_type=FixedAsset.DisposalType.SCRAPPED,
            reason="Utrangerad efter skada",
        )

        self.assertFalse(asset.is_active)
        self.assertEqual(asset.disposal_type, FixedAsset.DisposalType.SCRAPPED)

        with self.assertRaises(ValidationError):
            asset.register_monthly_depreciation()
        with self.assertRaises(ValidationError):
            asset.dispose(disposal_date=date(2026, 4, 1), disposal_type=FixedAsset.DisposalType.SOLD)

    def test_asset_type_key_change_blocked_when_type_is_used(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Använd typ",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        self.assertIsNotNone(asset.pk)

        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.EQUIPMENT)
        asset_type.key = "equipment_new"

        with self.assertRaises(ValidationError):
            asset_type.full_clean()

    def test_asset_type_deactivation_blocked_when_active_assets_use_type(self):
        FixedAsset.objects.create(
            company=self.company,
            name="Aktiv tillgång",
            asset_type=FixedAsset.AssetType.MACHINERY,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("1000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
            is_active=True,
        )

        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.MACHINERY)
        asset_type.is_active = False

        with self.assertRaises(ValidationError):
            asset_type.full_clean()

    def test_asset_type_delete_blocked_when_type_is_used(self):
        FixedAsset.objects.create(
            company=self.company,
            name="Tillgång att skydda",
            asset_type=FixedAsset.AssetType.COMPUTER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("5000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.COMPUTER)

        with self.assertRaises(ValidationError):
            asset_type.delete()

    def test_default_machinery_type_prefers_1219_when_available(self):
        company = create_company("PrefTest AB")
        create_account(company, "7830", "Avskrivningar", "7")
        create_account(company, "1229", "Ack avskrivningar inventarier", "1")
        create_account(company, "1219", "Ack avskrivningar maskiner", "1")

        ensure_default_asset_types(company)
        asset_type = FixedAssetType.objects.get(company=company, key=FixedAsset.AssetType.MACHINERY)
        self.assertIsNotNone(asset_type.accumulated_depreciation_account)
        self.assertEqual(asset_type.accumulated_depreciation_account.number, "1219")

    def test_default_intangible_type_prefers_1099_over_1079(self):
        create_account(self.company, "1099", "Ack avskrivningar övriga immateriella", "1")

        ensure_default_asset_types(self.company)
        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.INTANGIBLE)
        self.assertIsNotNone(asset_type.accumulated_depreciation_account)
        self.assertEqual(asset_type.accumulated_depreciation_account.number, "1099")

    def test_default_building_type_uses_7820_and_1119_when_available(self):
        create_account(self.company, "7820", "Avskrivningar byggnader", "7")
        create_account(self.company, "1119", "Ack avskrivningar byggnader", "1")

        ensure_default_asset_types(self.company)
        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.BUILDING)
        self.assertIsNotNone(asset_type.depreciation_expense_account)
        self.assertEqual(asset_type.depreciation_expense_account.number, "7820")
        self.assertIsNotNone(asset_type.accumulated_depreciation_account)
        self.assertEqual(asset_type.accumulated_depreciation_account.number, "1119")

    def test_default_land_type_is_seeded_without_depreciation_accounts(self):
        ensure_default_asset_types(self.company)
        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.LAND)
        self.assertIsNone(asset_type.depreciation_expense_account)
        self.assertIsNone(asset_type.accumulated_depreciation_account)

    def test_new_swedish_types_are_seeded(self):
        create_account(self.company, "7820", "Avskrivningar byggnader", "7")
        create_account(self.company, "7840", "Avskrivningar förbättringsutgifter", "7")
        create_account(self.company, "1129", "Ack avskrivningar förbättringsutgifter", "1")
        create_account(self.company, "1159", "Ack avskrivningar markanläggningar", "1")

        ensure_default_asset_types(self.company)

        self.assertTrue(
            FixedAssetType.objects.filter(company=self.company, key=FixedAsset.AssetType.LAND_IMPROVEMENTS).exists()
        )
        self.assertTrue(
            FixedAssetType.objects.filter(
                company=self.company, key=FixedAsset.AssetType.LEASEHOLD_IMPROVEMENTS
            ).exists()
        )
        self.assertTrue(FixedAssetType.objects.filter(company=self.company, key=FixedAsset.AssetType.PLOTS).exists())
        self.assertTrue(
            FixedAssetType.objects.filter(
                company=self.company, key=FixedAsset.AssetType.CONSTRUCTION_IN_PROGRESS
            ).exists()
        )

    def test_default_mapping_overwrites_old_machinery_account_with_better_value(self):
        old_acc = create_account(self.company, "1288", "Legacy ack avskrivningar", "1")
        better_acc = create_account(self.company, "1219", "Ack avskrivningar maskiner", "1")

        asset_type = FixedAssetType.objects.get(company=self.company, key=FixedAsset.AssetType.MACHINERY)
        asset_type.accumulated_depreciation_account = old_acc
        asset_type.name = "Gammalt namn"
        asset_type.sort_order = 999
        asset_type.save(update_fields=["accumulated_depreciation_account", "name", "sort_order"])

        ensure_default_asset_types(self.company)
        asset_type.refresh_from_db()
        self.assertEqual(asset_type.name, "Maskiner och tekniska anläggningar")
        self.assertEqual(asset_type.sort_order, 10)
        self.assertEqual(asset_type.accumulated_depreciation_account_id, better_acc.id)


class FixedAssetViewTests(CompanyTestCase):
    user_email = "test@example.com"
    company_name = "Viewbolaget AB"
    # Wider than the default year: the assets below depreciate from 2025.
    accounting_year_dates = (date(2025, 1, 1), date(2027, 12, 31))

    def setUp(self):
        super().setUp()
        create_accounts(
            self.company,
            [
                ("7830", "Avskrivningar", "7"),
                ("1229", "Ack avskrivningar inventarier", "1"),
                ("1249", "Ack avskrivningar bilar", "1"),
                ("7810", "Avskrivningar immateriella", "7"),
                ("1079", "Ack avskrivningar immateriella", "1"),
                ("1220", "Inventarier", "1"),
                ("3973", "Vinst vid avyttring", "3"),
                ("7973", "Förlust vid avyttring", "7"),
            ],
        )

    def test_list_shows_due_notice(self):
        FixedAsset.objects.create(
            company=self.company,
            name="Bil",
            asset_type=FixedAsset.AssetType.VEHICLE,
            acquisition_date=date(2025, 1, 15),
            depreciation_start_date=date(2025, 2, 1),
            acquisition_value=Decimal("240000.00"),
            salvage_value=Decimal("60000.00"),
            useful_life_months=60,
        )

        response = self.client.get(reverse("fixed_assets:asset_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Avskrivning påminnelse")

    def test_create_form_accepts_swedish_decimal_comma(self):
        response = self.client.post(
            reverse("fixed_assets:asset_create"),
            data={
                "name": "Server",
                "asset_type": FixedAsset.AssetType.COMPUTER,
                "acquisition_date": "2026-06-01",
                "depreciation_start_date": "2026-07-01",
                "acquisition_value": "10000,50",
                "salvage_value": "500,25",
                "useful_life_years": "3",
                "useful_life_extra_months": "0",
                "is_active": "on",
                "notes": "Inköp Q2",
            },
        )

        self.assertEqual(response.status_code, 302)
        asset = FixedAsset.objects.get(company=self.company, name="Server")
        self.assertEqual(asset.useful_life_months, 36)

    def test_create_form_converts_years_and_months_to_total_months(self):
        response = self.client.post(
            reverse("fixed_assets:asset_create"),
            data={
                "name": "Maskin med delår",
                "asset_type": FixedAsset.AssetType.MACHINERY,
                "acquisition_date": "2026-06-01",
                "depreciation_start_date": "2026-07-01",
                "acquisition_value": "12000",
                "salvage_value": "0",
                "useful_life_years": "2",
                "useful_life_extra_months": "6",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        asset = FixedAsset.objects.get(company=self.company, name="Maskin med delår")
        self.assertEqual(asset.useful_life_months, 30)

    def test_create_form_shows_depreciation_preview_and_monthly_date_guidance(self):
        response = self.client.get(reverse("fixed_assets:asset_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Avskrivningsbart belopp")
        self.assertContains(response, "Avskrivning beräknas månadsvis")

    def test_edit_form_hides_depreciation_sections_for_non_depreciable_type(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Markyta",
            asset_type=FixedAsset.AssetType.LAND,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 1, 15),
            acquisition_value=Decimal("50000.00"),
            salvage_value=Decimal("50000.00"),
            useful_life_months=1,
        )

        response = self.client.get(reverse("fixed_assets:asset_update", args=[asset.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'depreciation-start-wrapper" class="col-md-6 d-none')
        self.assertContains(response, 'salvage-wrapper" class="col-md-4 d-none')
        self.assertContains(response, 'useful-life-wrapper" class="col-md-4 d-none')
        self.assertContains(response, "inte avskrivningsbar")

    def test_create_non_depreciable_type_auto_sets_depreciation_values(self):
        response = self.client.post(
            reverse("fixed_assets:asset_create"),
            data={
                "name": "Tomt A",
                "asset_type": FixedAsset.AssetType.PLOTS,
                "acquisition_date": "2026-06-01",
                "acquisition_value": "100000",
                "is_active": "on",
                "notes": "Ej avskrivningsbar",
            },
        )

        self.assertEqual(response.status_code, 302)
        asset = FixedAsset.objects.get(company=self.company, name="Tomt A")
        self.assertEqual(asset.salvage_value, Decimal("100000.00"))
        self.assertEqual(asset.useful_life_months, 1)
        self.assertEqual(asset.depreciation_start_date, asset.acquisition_date)

    def test_topbar_alert_bell_hidden_when_no_due_depreciation(self):
        response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-ack-url="')

    def test_topbar_alert_bell_links_to_fixed_assets_when_due(self):
        FixedAsset.objects.create(
            company=self.company,
            name="Truck",
            asset_type=FixedAsset.AssetType.VEHICLE,
            acquisition_date=date(2025, 1, 15),
            depreciation_start_date=date(2025, 2, 1),
            acquisition_value=Decimal("240000.00"),
            salvage_value=Decimal("60000.00"),
            useful_life_months=60,
        )

        response = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fixed-assets-alert-menu")
        self.assertContains(response, reverse("fixed_assets:asset_list"))

    def test_acknowledged_alert_is_not_highlighted_until_new_signature(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Maskin A",
            asset_type=FixedAsset.AssetType.MACHINERY,
            acquisition_date=date(2025, 1, 15),
            depreciation_start_date=date(2025, 2, 1),
            acquisition_value=Decimal("100000.00"),
            salvage_value=Decimal("10000.00"),
            useful_life_months=60,
        )

        response_before = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertContains(response_before, "topbar-alert-bell text-danger")

        ack_response = self.client.post(reverse("fixed_assets:acknowledge_alerts"))
        self.assertEqual(ack_response.status_code, 200)

        response_after = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertContains(response_after, "fixed-assets-alert-menu")
        self.assertNotContains(response_after, "topbar-alert-bell text-danger")

        asset.register_monthly_depreciation()
        response_after_new_alert = self.client.get(reverse("bookkeeping:dashboard"))
        self.assertContains(response_after_new_alert, "topbar-alert-bell text-danger")

    def test_detail_shows_non_depreciable_message_when_residual_equals_purchase(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Ej avskrivningsbar",
            asset_type=FixedAsset.AssetType.OTHER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("10000.00"),
            salvage_value=Decimal("10000.00"),
            useful_life_months=12,
        )

        response = self.client.get(reverse("fixed_assets:asset_detail", args=[asset.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "inget avskrivningsbart belopp")

    def test_depreciation_transaction_link_contains_return_to_asset_detail(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Linktest",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        dep = asset.register_monthly_depreciation(user=self.user)

        detail_url = reverse("fixed_assets:asset_detail", args=[asset.pk])
        expected_txn_link = reverse("bookkeeping:transaction_detail", args=[dep.transaction.pk])
        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{expected_txn_link}?return_to={detail_url}",
        )

    def test_asset_type_list_page_available(self):
        response = self.client.get(reverse("fixed_assets:asset_type_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tillgångstyper")

    def test_asset_type_create_page_creates_type_with_accounts(self):
        expense = Account.objects.get(company=self.company, number="7830")
        accumulated = Account.objects.get(company=self.company, number="1229")

        response = self.client.post(
            reverse("fixed_assets:asset_type_create"),
            data={
                "name": "Specialinventarier",
                "key": "special_inventory",
                "depreciation_expense_account": expense.pk,
                "accumulated_depreciation_account": accumulated.pk,
                "sort_order": 99,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(FixedAssetType.objects.filter(company=self.company, key="special_inventory").exists())

    def test_edit_page_shows_delete_button_with_confirmation(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Raderbar",
            asset_type=FixedAsset.AssetType.OTHER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("1000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        response = self.client.get(reverse("fixed_assets:asset_update", args=[asset.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Radera tillgång")
        self.assertContains(response, "Är du säker på att du vill radera tillgången?")
        self.assertContains(response, f'formaction="{reverse("fixed_assets:asset_delete", args=[asset.pk])}"')

    def test_asset_can_be_deleted_from_edit_page(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Ta bort mig",
            asset_type=FixedAsset.AssetType.OTHER,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("1000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        response = self.client.post(reverse("fixed_assets:asset_delete", args=[asset.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(FixedAsset.objects.filter(pk=asset.pk).exists())

    def test_asset_cannot_be_deleted_when_depreciations_exist(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Skyddad",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        depreciation = asset.register_monthly_depreciation(user=self.user)
        transaction_pk = depreciation.transaction.pk

        response = self.client.post(reverse("fixed_assets:asset_delete", args=[asset.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(FixedAsset.objects.filter(pk=asset.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=transaction_pk).exists())
        self.assertTrue(FixedAssetDepreciation.objects.filter(pk=depreciation.pk).exists())

    def test_asset_with_history_can_be_disposed_instead(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Skadad",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        asset.register_monthly_depreciation(user=self.user)

        response = self.client.post(
            reverse("fixed_assets:asset_dispose", args=[asset.pk]),
            {
                "disposal_date": "2026-03-01",
                "disposal_type": FixedAsset.DisposalType.SCRAPPED,
                "disposal_reason": "Utrangerad efter skada",
            },
        )
        self.assertEqual(response.status_code, 302)
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.disposal_type, FixedAsset.DisposalType.SCRAPPED)
        self.assertEqual(str(asset.disposed_at), "2026-03-01")

        # Once disposed, the asset still cannot be hard-deleted, and no further
        # depreciation may be registered against it.
        delete_response = self.client.post(reverse("fixed_assets:asset_delete", args=[asset.pk]))
        self.assertEqual(delete_response.status_code, 302)
        self.assertTrue(FixedAsset.objects.filter(pk=asset.pk).exists())
        with self.assertRaises(ValidationError):
            asset.register_monthly_depreciation(user=self.user)

    def test_impairment_can_be_registered_and_posts_a_balanced_transaction(self):
        create_account(self.company, "7732", "Nedskrivningar inventarier", "7")
        create_account(self.company, "1228", "Ack nedskrivningar inventarier", "1")

        asset = FixedAsset.objects.create(
            company=self.company,
            name="Skadad maskin",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )

        response = self.client.post(
            reverse("fixed_assets:asset_register_impairment", args=[asset.pk]),
            {"period": "2026-03-01", "amount": "2000", "reason": "Skadad vid transport"},
        )
        self.assertEqual(response.status_code, 302)

        asset.refresh_from_db()
        self.assertEqual(asset.total_impaired, Decimal("2000.00"))
        self.assertEqual(asset.current_book_value, Decimal("10000.00"))

        impairment = asset.impairment_entries.get()
        self.assertIsNotNone(impairment.transaction)
        impairment.transaction.validate_balanced()

    def test_asset_type_change_is_recorded_as_reclassification(self):
        asset = FixedAsset.objects.create(
            company=self.company,
            name="Flyttad",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
            is_active=True,
        )

        response = self.client.post(
            reverse("fixed_assets:asset_update", args=[asset.pk]),
            {
                "name": asset.name,
                "asset_type": FixedAsset.AssetType.COMPUTER,
                "acquisition_date": "2026-01-15",
                "depreciation_start_date": "2026-02-01",
                "acquisition_value": "12000",
                "salvage_value": "0",
                "useful_life_years": "1",
                "useful_life_extra_months": "0",
                "is_active": "on",
                "reclassification_reason": "Omklassificerad till dator",
            },
        )
        self.assertEqual(response.status_code, 302)

        reclass = asset.reclassification_entries.get()
        self.assertEqual(reclass.from_asset_type, FixedAsset.AssetType.EQUIPMENT)
        self.assertEqual(reclass.to_asset_type, FixedAsset.AssetType.COMPUTER)
        self.assertEqual(reclass.reason, "Omklassificerad till dator")


class FixedAssetDisposalBookingTests(TestCase):
    def setUp(self):
        self.company = create_company("Avgångsbolaget AB")
        self.accounting_year = create_accounting_year(self.company, date(2026, 1, 1), date(2026, 12, 31))
        self.accounts = create_accounts(
            self.company,
            [
                ("7830", "Avskrivningar", "7"),
                ("1229", "Ack avskrivningar inventarier", "1"),
                ("7732", "Nedskrivningar inventarier", "7"),
                ("1228", "Ack nedskrivningar inventarier", "1"),
                ("1220", "Inventarier", "1"),
                ("1930", "Företagskonto", "1"),
                ("3973", "Vinst vid avyttring", "3"),
                ("7973", "Förlust vid avyttring", "7"),
            ],
        )
        ensure_default_asset_types(self.company)
        self.asset = FixedAsset.objects.create(
            company=self.company,
            name="Maskin",
            asset_type=FixedAsset.AssetType.EQUIPMENT,
            acquisition_date=date(2026, 1, 15),
            depreciation_start_date=date(2026, 2, 1),
            acquisition_value=Decimal("12000.00"),
            salvage_value=Decimal("0.00"),
            useful_life_months=12,
        )
        self.asset.register_monthly_depreciation()  # 1000 kr -> bokfört värde 11000

    def _net(self, number):
        entries = self.asset.disposal_transaction.entries.filter(account=self.accounts[number])
        return sum(e.debit - e.credit for e in entries)

    def test_sale_with_gain_books_balanced_voucher(self):
        self.asset.dispose(
            disposal_date=date(2026, 3, 1),
            disposal_type=FixedAsset.DisposalType.SOLD,
            sale_price=Decimal("11500.00"),
            proceeds_account=self.accounts["1930"],
        )
        txn = self.asset.disposal_transaction
        self.assertIsNotNone(txn)
        txn.validate_balanced()
        self.assertEqual(self._net("1220"), Decimal("-12000.00"))
        self.assertEqual(self._net("1229"), Decimal("1000.00"))
        self.assertEqual(self._net("1930"), Decimal("11500.00"))
        self.assertEqual(self._net("3973"), Decimal("-500.00"))

    def test_scrapping_books_loss_of_remaining_book_value(self):
        self.asset.dispose(disposal_date=date(2026, 3, 1), disposal_type=FixedAsset.DisposalType.SCRAPPED)
        self.assertEqual(self._net("7973"), Decimal("11000.00"))
        self.assertEqual(self._net("1220"), Decimal("-12000.00"))

    def test_sale_price_requires_proceeds_account(self):
        with self.assertRaises(ValidationError):
            self.asset.dispose(
                disposal_date=date(2026, 3, 1), disposal_type=FixedAsset.DisposalType.SOLD, sale_price=Decimal("1")
            )
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.is_disposed)

    def test_missing_asset_account_blocks_disposal(self):
        # Inaktivt konto plockas inte av seed-synken -> typen står utan tillgångskonto.
        Account.objects.filter(pk=self.accounts["1220"].pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            self.asset.dispose(disposal_date=date(2026, 3, 1), disposal_type=FixedAsset.DisposalType.SCRAPPED)
