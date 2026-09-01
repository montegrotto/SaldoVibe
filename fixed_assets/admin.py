from django.contrib import admin

from .models import FixedAsset, FixedAssetDepreciation, FixedAssetType


@admin.register(FixedAssetType)
class FixedAssetTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "key",
        "company",
        "depreciation_expense_account",
        "accumulated_depreciation_account",
        "is_active",
    )
    list_filter = ("company", "is_active")
    search_fields = ("name", "key")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FixedAsset)
class FixedAssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "asset_type_name",
        "acquisition_value",
        "current_book_value",
        "next_depreciation_date",
        "is_active",
    )
    list_filter = ("company", "asset_type", "is_active")
    search_fields = ("name",)


@admin.register(FixedAssetDepreciation)
class FixedAssetDepreciationAdmin(admin.ModelAdmin):
    list_display = ("fixed_asset", "period", "amount", "created_at")
    list_filter = ("period",)
    search_fields = ("fixed_asset__name",)
