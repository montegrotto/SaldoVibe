from django.contrib import admin

from .models import BankAccount, BankImport, BankTransaction


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "account_type", "bookkeeping_account", "is_active")
    list_filter = ("company", "account_type", "is_active")
    search_fields = ("name", "account_number")


@admin.register(BankImport)
class BankImportAdmin(admin.ModelAdmin):
    list_display = ("source_name", "company", "bank_account", "imported_at", "imported_by")
    list_filter = ("company", "source_name", "imported_at")


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "company", "bank_account", "description", "amount", "is_booked")
    list_filter = ("company", "bank_account", "is_booked")
    search_fields = ("description", "external_id")
