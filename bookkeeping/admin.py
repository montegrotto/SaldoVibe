from django.contrib import admin

from .models import (
    Account,
    AccountingYear,
    BudgetLine,
    Company,
    CompanyMembership,
    JournalEntry,
    PeriodLock,
    Transaction,
    VerificationTemplate,
    VerificationTemplateEntry,
    VoucherSeries,
    VoucherSeriesRule,
)


class CompanyMembershipInline(admin.TabularInline):
    model = CompanyMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "org_number", "company_icon", "vat_reporting_period", "is_active")
    search_fields = ("name", "org_number")
    inlines = (CompanyMembershipInline,)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "company", "account_class", "is_active")
    list_filter = ("company", "account_class", "is_active")
    search_fields = ("number", "name")
    ordering = ("number",)


class JournalEntryInline(admin.TabularInline):
    model = JournalEntry
    extra = 0
    fields = ("account", "debit", "credit", "description")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Read-only: bokförda verifikationer får bara skapas/korrigeras via appens egna
    flöden (voucher-numrering, periodlås, korrigeringsverifikationer) - se
    transaction_add/transaction_reverse i bookkeeping/views/transactions.py. Databasen
    stoppar även själva skrivningen, se bookkeeping/migrations/0036_lock_posted_transactions.py.
    """

    list_display = (
        "date",
        "accounting_year",
        "voucher_series",
        "voucher_number",
        "source",
        "description",
        "reference",
        "created_by",
        "created_at",
    )
    list_filter = ("accounting_year", "source", "date")
    search_fields = ("description", "reference")
    inlines = [JournalEntryInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccountingYear)
class AccountingYearAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "start_date", "end_date")
    list_filter = ("company",)
    ordering = ("-start_date",)


@admin.register(VoucherSeries)
class VoucherSeriesAdmin(admin.ModelAdmin):
    list_display = ("company", "accounting_year", "code", "next_number", "is_active")
    list_filter = ("company", "accounting_year", "is_active")
    search_fields = ("company__name", "code")


@admin.register(VoucherSeriesRule)
class VoucherSeriesRuleAdmin(admin.ModelAdmin):
    list_display = ("company", "source", "series_code")
    list_filter = ("company", "source")
    search_fields = ("company__name", "series_code")


@admin.register(PeriodLock)
class PeriodLockAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "accounting_year",
        "period_start",
        "period_end",
        "is_locked",
        "locked_by",
        "locked_at",
        "reopened_by",
        "reopened_at",
    )
    list_filter = ("company", "is_locked")
    search_fields = ("company__name", "reason", "reopened_reason")


class VerificationTemplateEntryInline(admin.TabularInline):
    model = VerificationTemplateEntry
    extra = 2
    fields = ("account", "is_debit", "is_credit", "sort_order")


@admin.register(VerificationTemplate)
class VerificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_active", "updated_at")
    list_filter = ("company", "is_active")
    search_fields = ("name", "description")
    inlines = [VerificationTemplateEntryInline]


@admin.register(BudgetLine)
class BudgetLineAdmin(admin.ModelAdmin):
    list_display = ("company", "accounting_year", "account", "month", "amount")
    list_filter = ("company", "accounting_year")
    search_fields = ("account__number", "account__name")
