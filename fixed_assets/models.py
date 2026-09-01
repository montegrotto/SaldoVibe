import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

NON_DEPRECIABLE_ASSET_TYPE_KEYS = {
    "land",
    "plots",
    "construction_in_progress",
}


def _month_start(value):
    return value.replace(day=1)


def _add_months(value, months):
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    return value.replace(year=year, month=month)


def get_due_alert_state_for_company(company):
    """Return due assets, count, and a deterministic signature for alert acknowledgement."""
    if company is None:
        return [], 0, ""

    assets = (
        FixedAsset.objects.filter(company=company, is_active=True)
        .prefetch_related("depreciation_entries")
        .order_by("pk")
    )
    due_assets = [asset for asset in assets if asset.is_due_for_depreciation]
    signature_parts = [
        f"{asset.pk}:{asset.next_depreciation_date.isoformat() if asset.next_depreciation_date else ''}"
        for asset in due_assets
    ]
    signature = "|".join(signature_parts)
    return due_assets, len(due_assets), signature


def _slugify_asset_type_key(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_\-\s]", "", value)
    value = re.sub(r"[\s\-]+", "_", value)
    return value[:40] or "asset_type"


class FixedAssetType(models.Model):
    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="fixed_asset_types",
        verbose_name="Företag",
    )
    key = models.CharField("Nyckel", max_length=40)
    name = models.CharField("Namn", max_length=120)
    depreciation_expense_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_expense",
        verbose_name="Avskrivningskonto (debet)",
    )
    accumulated_depreciation_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_accumulated",
        verbose_name="Ackumulerad avskrivning (kredit)",
    )
    impairment_expense_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_impairment_expense",
        verbose_name="Nedskrivningskostnad (debet)",
    )
    accumulated_impairment_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_accumulated_impairment",
        verbose_name="Ackumulerad nedskrivning (kredit)",
    )
    # Avyttring/utrangering: tillgångskontot bokas bort, resultatet hamnar på vinst-/förlustkontot.
    asset_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_asset",
        verbose_name="Tillgångskonto",
    )
    disposal_gain_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_disposal_gain",
        verbose_name="Vinst vid avyttring (kredit)",
    )
    disposal_loss_account = models.ForeignKey(
        "bookkeeping.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fixed_asset_type_disposal_loss",
        verbose_name="Förlust vid avyttring (debet)",
    )
    is_active = models.BooleanField("Aktiv", default=True)
    sort_order = models.PositiveIntegerField("Sorteringsordning", default=0)

    ACCOUNT_FIELDS = (
        "depreciation_expense_account",
        "accumulated_depreciation_account",
        "impairment_expense_account",
        "accumulated_impairment_account",
        "asset_account",
        "disposal_gain_account",
        "disposal_loss_account",
    )

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Anläggningstillgångstyp"
        verbose_name_plural = "Anläggningstillgångstyper"
        constraints = [
            models.UniqueConstraint(fields=["company", "key"], name="uniq_fixed_asset_type_key_per_company"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.key:
            self.key = self.name
        self.key = _slugify_asset_type_key(self.key)

        if self.pk:
            previous = FixedAssetType.objects.filter(pk=self.pk).values("key", "is_active").first()
            if previous:
                used_assets = FixedAsset.objects.filter(company=self.company, asset_type=previous["key"])
                if previous["key"] != self.key and used_assets.exists():
                    raise ValidationError(
                        {"key": "Nyckeln kan inte ändras eftersom tillgångstypen används av befintliga tillgångar."}
                    )
                if previous["is_active"] and not self.is_active and used_assets.filter(is_active=True).exists():
                    raise ValidationError(
                        {"is_active": "Tillgångstypen kan inte inaktiveras så länge aktiva tillgångar använder den."}
                    )

        for field_name in self.ACCOUNT_FIELDS:
            account = getattr(self, field_name)
            if account and account.company_id != self.company_id:
                raise ValidationError({field_name: "Kontot tillhör ett annat företag."})

    def delete(self, *args, **kwargs):
        if FixedAsset.objects.filter(company=self.company, asset_type=self.key).exists():
            raise ValidationError("Tillgångstypen kan inte tas bort eftersom den används av befintliga tillgångar.")
        return super().delete(*args, **kwargs)


def ensure_default_asset_types(company):
    if company is None:
        return

    def pick_account(candidate_numbers):
        for number in candidate_numbers:
            account = company.accounts.filter(is_active=True, number=number).first()
            if account is not None:
                return account
        return None

    # depreciation_* and impairment_* are BAS account candidates (tried in order, first
    # match on the company's actual chart of accounts wins). Impairment mapping per
    # BAS-intressenternas förening's SRU-kopplingar: 7710/7720/7731/7732/7733 (expense)
    # against 1018-1098/1118/1158/1218/1228/1298 (accumulated), mirroring the existing
    # depreciation pairing per asset type. Non-depreciable types keep empty tuples.
    defaults = [
        {
            "key": "machinery",
            "asset": ("1210",),
            "gain": ("3973",),
            "loss": ("7973",),
            "name": "Maskiner och tekniska anläggningar",
            "sort_order": 10,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1219", "1229"),
            "impairment": ("7731",),
            "accumulated_impairment": ("1218",),
        },
        {
            "key": "equipment",
            "asset": ("1220",),
            "gain": ("3973",),
            "loss": ("7973",),
            "name": "Inventarier, verktyg och installationer",
            "sort_order": 20,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1229",),
            "impairment": ("7732",),
            "accumulated_impairment": ("1228",),
        },
        {
            "key": "computer",
            "asset": ("1220",),
            "gain": ("3973",),
            "loss": ("7973",),
            "name": "Datorer och IT-utrustning",
            "sort_order": 30,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1229",),
            "impairment": ("7732",),
            "accumulated_impairment": ("1228",),
        },
        {
            "key": "vehicle",
            "asset": ("1240", "1220"),
            "gain": ("3973",),
            "loss": ("7973",),
            "name": "Bilar och transportmedel",
            "sort_order": 40,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1249", "1229"),
            "impairment": ("7733",),
            "accumulated_impairment": ("1298",),
        },
        {
            "key": "building",
            "asset": ("1110",),
            "gain": ("3972",),
            "loss": ("7972",),
            "name": "Byggnader",
            "sort_order": 50,
            "depreciation": ("7820", "7830"),
            "accumulated_depreciation": ("1119",),
            "impairment": ("7720",),
            "accumulated_impairment": ("1118",),
        },
        {
            "key": "land",
            "asset": ("1130",),
            "gain": ("3972",),
            "loss": ("7972",),
            "name": "Mark (ej avskrivningsbar)",
            "sort_order": 60,
            "depreciation": (),
            "accumulated_depreciation": (),
            "impairment": (),
            "accumulated_impairment": (),
        },
        {
            "key": "land_improvements",
            "asset": ("1150",),
            "gain": ("3972",),
            "loss": ("7972",),
            "name": "Markanläggningar",
            "sort_order": 70,
            "depreciation": ("7820", "7830"),
            "accumulated_depreciation": ("1159",),
            "impairment": ("7720",),
            "accumulated_impairment": ("1158",),
        },
        {
            "key": "leasehold_improvements",
            "asset": ("1120",),
            "gain": ("3972",),
            "loss": ("7972",),
            "name": "Förbättringsutgifter på annans fastighet",
            "sort_order": 80,
            "depreciation": ("7840", "7820", "7830"),
            "accumulated_depreciation": ("1129",),
            "impairment": ("7733",),
            "accumulated_impairment": ("1298",),
        },
        {
            "key": "plots",
            "asset": ("1130",),
            "gain": ("3972",),
            "loss": ("7972",),
            "name": "Tomter och obebyggd mark (ej avskrivningsbar)",
            "sort_order": 90,
            "depreciation": (),
            "accumulated_depreciation": (),
            "impairment": (),
            "accumulated_impairment": (),
        },
        {
            "key": "construction_in_progress",
            "asset": ("1180",),
            "gain": ("3970",),
            "loss": ("7970",),
            "name": "Pågående nyanläggning/förskott (ej avskrivningsbar)",
            "sort_order": 100,
            "depreciation": (),
            "accumulated_depreciation": (),
            "impairment": (),
            "accumulated_impairment": (),
        },
        {
            "key": "building_inventory",
            "asset": ("1222", "1220"),
            "gain": ("3973",),
            "loss": ("7973",),
            "name": "Byggnadsinventarier",
            "sort_order": 110,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1229", "1219"),
            "impairment": ("7732",),
            "accumulated_impairment": ("1228",),
        },
        {
            "key": "intangible",
            "asset": ("1010", "1070"),
            "gain": ("3971",),
            "loss": ("7971",),
            "name": "Immateriella anläggningstillgångar",
            "sort_order": 120,
            "depreciation": ("7810",),
            "accumulated_depreciation": ("1099", "1079"),
            "impairment": ("7710",),
            "accumulated_impairment": ("1098", "1078"),
        },
        {
            "key": "other",
            "asset": ("1290",),
            "gain": ("3970",),
            "loss": ("7970",),
            "name": "Övriga anläggningstillgångar",
            "sort_order": 130,
            "depreciation": ("7830",),
            "accumulated_depreciation": ("1229", "1219"),
            "impairment": ("7733",),
            "accumulated_impairment": ("1298",),
        },
    ]

    for row in defaults:
        accounts = {
            "depreciation_expense_account": pick_account(row["depreciation"]),
            "accumulated_depreciation_account": pick_account(row["accumulated_depreciation"]),
            "impairment_expense_account": pick_account(row["impairment"]),
            "accumulated_impairment_account": pick_account(row["accumulated_impairment"]),
            "asset_account": pick_account(row["asset"]),
            "disposal_gain_account": pick_account(row["gain"]),
            "disposal_loss_account": pick_account(row["loss"]),
        }
        obj, created = FixedAssetType.objects.get_or_create(
            company=company,
            key=row["key"],
            defaults={"name": row["name"], "sort_order": row["sort_order"], "is_active": True, **accounts},
        )
        if not created:
            desired = {"name": row["name"], "sort_order": row["sort_order"], "is_active": True, **accounts}
            changed_fields = [name for name, value in desired.items() if getattr(obj, name) != value]
            for name in changed_fields:
                setattr(obj, name, desired[name])
            if changed_fields:
                obj.save(update_fields=changed_fields)


class FixedAsset(models.Model):
    class DisposalType(models.TextChoices):
        SCRAPPED = "scrapped", "Utrangerad (skrotad)"
        SOLD = "sold", "Såld/avyttrad"
        OTHER = "other", "Övrigt"

    class AssetType(models.TextChoices):
        MACHINERY = "machinery", "Maskiner och tekniska anläggningar"
        EQUIPMENT = "equipment", "Inventarier, verktyg och installationer"
        COMPUTER = "computer", "Datorer och IT-utrustning"
        VEHICLE = "vehicle", "Bilar och transportmedel"
        BUILDING = "building", "Byggnader"
        LAND = "land", "Mark"
        LAND_IMPROVEMENTS = "land_improvements", "Markanläggningar"
        LEASEHOLD_IMPROVEMENTS = "leasehold_improvements", "Förbättringsutgifter på annans fastighet"
        PLOTS = "plots", "Tomter och obebyggd mark"
        CONSTRUCTION_IN_PROGRESS = "construction_in_progress", "Pågående nyanläggning/förskott"
        BUILDING_INVENTORY = "building_inventory", "Byggnadsinventarier"
        INTANGIBLE = "intangible", "Immateriella anläggningstillgångar"
        OTHER = "other", "Övriga anläggningstillgångar"

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="fixed_assets",
        verbose_name="Företag",
    )
    name = models.CharField("Namn", max_length=200)
    asset_type = models.CharField("Typ", max_length=40)
    acquisition_date = models.DateField("Inköpsdatum")
    depreciation_start_date = models.DateField("Start avskrivning")
    acquisition_value = models.DecimalField("Anskaffningsvärde", max_digits=15, decimal_places=2)
    salvage_value = models.DecimalField("Restvärde", max_digits=15, decimal_places=2, default=Decimal("0.00"))
    useful_life_months = models.PositiveIntegerField("Nyttjandeperiod (månader)")
    is_active = models.BooleanField("Aktiv", default=True)
    notes = models.TextField("Anteckningar", blank=True)
    disposed_at = models.DateField("Utrangerings-/avyttringsdatum", null=True, blank=True)
    disposal_type = models.CharField("Typ av avgång", max_length=20, choices=DisposalType.choices, blank=True)
    disposal_reason = models.TextField("Anledning till avgång", blank=True)
    # SET_NULL: gallring av äldre år får inte hänga (docs/compliance/gdpr/retention-purge-design.md).
    disposal_transaction = models.ForeignKey(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_asset_disposals",
        verbose_name="Avgångsverifikation",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-depreciation_start_date", "name"]
        verbose_name = "Anläggningstillgång"
        verbose_name_plural = "Anläggningstillgångar"

    def __str__(self):
        return self.name

    def clean(self):
        if self.salvage_value < Decimal("0.00"):
            raise ValidationError({"salvage_value": "Restvärde kan inte vara negativt."})
        if self.acquisition_value <= Decimal("0.00"):
            raise ValidationError({"acquisition_value": "Anskaffningsvärdet måste vara större än 0."})
        if self.salvage_value > self.acquisition_value:
            raise ValidationError({"salvage_value": "Restvärde kan inte vara högre än anskaffningsvärdet."})
        if self.salvage_value == self.acquisition_value and self.pk and self.depreciation_entries.exists():
            raise ValidationError(
                {"salvage_value": "Restvärde kan inte vara samma som anskaffningsvärde efter påbörjad avskrivning."}
            )
        if self.useful_life_months <= 0:
            raise ValidationError({"useful_life_months": "Nyttjandeperiod måste vara minst 1 månad."})

    @property
    def depreciable_amount(self):
        return self.acquisition_value - self.salvage_value

    @property
    def monthly_depreciation_amount(self):
        if self.useful_life_months == 0:
            return Decimal("0.00")
        return (self.depreciable_amount / Decimal(self.useful_life_months)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def depreciation_count(self):
        return self.depreciation_entries.filter(is_correction=False).count()

    @property
    def is_fully_depreciated(self):
        if self.depreciable_amount <= Decimal("0.00"):
            return True
        return self.depreciation_count >= self.useful_life_months or self.total_depreciated >= self.depreciable_amount

    @property
    def total_depreciated(self):
        total = self.depreciation_entries.aggregate(total=models.Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def total_impaired(self):
        total = self.impairment_entries.aggregate(total=models.Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def current_book_value(self):
        return self.acquisition_value - self.total_depreciated - self.total_impaired

    @property
    def is_disposed(self):
        return self.disposed_at is not None

    @property
    def next_depreciation_date(self):
        if self.is_fully_depreciated:
            return None
        return _month_start(_add_months(self.depreciation_start_date, self.depreciation_count))

    @property
    def is_due_for_depreciation(self):
        next_date = self.next_depreciation_date
        if next_date is None:
            return False
        return next_date <= _month_start(timezone.localdate())

    @property
    def due_months(self):
        next_date = self.next_depreciation_date
        if next_date is None:
            return 0
        today_month = _month_start(timezone.localdate())
        if next_date > today_month:
            return 0
        return (today_month.year - next_date.year) * 12 + (today_month.month - next_date.month)

    def next_depreciation_amount(self):
        remaining = self.depreciable_amount - self.total_depreciated
        if remaining <= Decimal("0.00"):
            return Decimal("0.00")
        if self.depreciation_count + 1 >= self.useful_life_months:
            return remaining.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        monthly = self.monthly_depreciation_amount
        if monthly > remaining:
            return remaining.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return monthly

    def register_monthly_depreciation(self, period=None, user=None):
        from .services import register_monthly_depreciation

        return register_monthly_depreciation(self, period=period, user=user)

    def register_depreciation_correction(self, original_entry, amount, reason, user=None):
        from .services import register_depreciation_correction

        return register_depreciation_correction(self, original_entry, amount, reason, user=user)

    def register_impairment(self, period, amount, reason, user=None):
        from .services import register_impairment

        return register_impairment(self, period, amount, reason, user=user)

    def register_impairment_correction(self, original_entry, amount, reason, user=None):
        from .services import register_impairment_correction

        return register_impairment_correction(self, original_entry, amount, reason, user=user)

    @property
    def asset_type_name(self):
        asset_type = FixedAssetType.objects.filter(company=self.company, key=self.asset_type).first()
        if asset_type:
            return asset_type.name
        fallback = dict(self.AssetType.choices)
        return fallback.get(self.asset_type, self.asset_type)

    def get_asset_type_display(self):
        return self.asset_type_name

    def dispose(
        self, disposal_date, disposal_type, reason="", user=None, sale_price=Decimal("0.00"), proceeds_account=None
    ):
        from django.db import transaction as db_transaction

        from .services import create_disposal_transaction

        if self.disposed_at:
            raise ValidationError("Tillgången är redan avyttrad/utrangerad.")
        if disposal_type not in self.DisposalType.values:
            raise ValidationError({"disposal_type": "Ogiltig typ av avgång."})
        if disposal_date < self.depreciation_start_date:
            raise ValidationError({"disposal_date": "Avgångsdatum kan inte vara före avskrivningsstart."})
        sale_price = sale_price or Decimal("0.00")
        if sale_price < Decimal("0.00"):
            raise ValidationError({"sale_price": "Försäljningspris kan inte vara negativt."})
        if sale_price > Decimal("0.00") and proceeds_account is None:
            raise ValidationError({"proceeds_account": "Ange vilket konto försäljningspriset bokförs mot."})

        with db_transaction.atomic():
            self.disposal_transaction = create_disposal_transaction(
                self, disposal_date, sale_price=sale_price, proceeds_account=proceeds_account, user=user
            )
            self.disposed_at = disposal_date
            self.disposal_type = disposal_type
            self.disposal_reason = reason
            self.is_active = False
            self.save(
                update_fields=[
                    "disposed_at",
                    "disposal_type",
                    "disposal_reason",
                    "disposal_transaction",
                    "is_active",
                    "updated_at",
                ]
            )
        return self


class FixedAssetDepreciation(models.Model):
    fixed_asset = models.ForeignKey(
        FixedAsset,
        on_delete=models.CASCADE,
        related_name="depreciation_entries",
        verbose_name="Anläggningstillgång",
    )
    period = models.DateField("Period")
    amount = models.DecimalField("Belopp", max_digits=15, decimal_places=2)
    transaction = models.OneToOneField(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_asset_depreciation",
        verbose_name="Verifikation",
    )
    is_correction = models.BooleanField("Är korrigering", default=False)
    correction_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
        verbose_name="Korrigering av",
    )
    reason = models.TextField("Anledning", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "-id"]
        verbose_name = "Avskrivning"
        verbose_name_plural = "Avskrivningar"
        constraints = [
            models.UniqueConstraint(
                fields=["fixed_asset", "period"],
                condition=models.Q(is_correction=False),
                name="uniq_fixed_asset_depreciation_period",
            ),
        ]

    def __str__(self):
        return f"{self.fixed_asset} {self.period}: {self.amount}"

    def clean(self):
        if self.is_correction:
            if self.amount == Decimal("0.00"):
                raise ValidationError({"amount": "Korrigeringsbeloppet kan inte vara noll."})
        elif self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Avskrivningsbelopp måste vara större än 0."})


class FixedAssetImpairment(models.Model):
    fixed_asset = models.ForeignKey(
        FixedAsset,
        on_delete=models.CASCADE,
        related_name="impairment_entries",
        verbose_name="Anläggningstillgång",
    )
    period = models.DateField("Period")
    amount = models.DecimalField("Belopp", max_digits=15, decimal_places=2)
    transaction = models.OneToOneField(
        "bookkeeping.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_asset_impairment",
        verbose_name="Verifikation",
    )
    is_correction = models.BooleanField("Är korrigering", default=False)
    correction_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
        verbose_name="Korrigering av",
    )
    reason = models.TextField("Anledning")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period", "-id"]
        verbose_name = "Nedskrivning"
        verbose_name_plural = "Nedskrivningar"
        constraints = [
            models.UniqueConstraint(
                fields=["fixed_asset", "period"],
                condition=models.Q(is_correction=False),
                name="uniq_fixed_asset_impairment_period",
            ),
        ]

    def __str__(self):
        return f"{self.fixed_asset} {self.period}: {self.amount}"

    def clean(self):
        if not self.reason:
            raise ValidationError({"reason": "Ange en anledning till nedskrivningen."})
        if self.is_correction:
            if self.amount == Decimal("0.00"):
                raise ValidationError({"amount": "Korrigeringsbeloppet kan inte vara noll."})
        elif self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Nedskrivningsbelopp måste vara större än 0."})


class FixedAssetReclassification(models.Model):
    fixed_asset = models.ForeignKey(
        FixedAsset,
        on_delete=models.CASCADE,
        related_name="reclassification_entries",
        verbose_name="Anläggningstillgång",
    )
    changed_at = models.DateTimeField("Ändrad", auto_now_add=True)
    from_asset_type = models.CharField("Från typ", max_length=40)
    to_asset_type = models.CharField("Till typ", max_length=40)
    reason = models.TextField("Anledning", blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fixed_asset_reclassifications",
        verbose_name="Ändrad av",
    )

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name = "Omklassificering"
        verbose_name_plural = "Omklassificeringar"

    def __str__(self):
        return f"{self.fixed_asset}: {self.from_asset_type} -> {self.to_asset_type}"

    def _asset_type_display(self, key):
        asset_type = FixedAssetType.objects.filter(company=self.fixed_asset.company, key=key).first()
        if asset_type:
            return asset_type.name
        fallback = dict(FixedAsset.AssetType.choices)
        return fallback.get(key, key)

    @property
    def from_asset_type_name(self):
        return self._asset_type_display(self.from_asset_type)

    @property
    def to_asset_type_name(self):
        return self._asset_type_display(self.to_asset_type)
