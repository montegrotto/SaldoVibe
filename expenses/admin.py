from django.contrib import admin

from .models import ExpenseClaim, ExpenseClaimPayment


class ExpenseClaimPaymentInline(admin.TabularInline):
    model = ExpenseClaimPayment
    extra = 0


@admin.register(ExpenseClaim)
class ExpenseClaimAdmin(admin.ModelAdmin):
    list_display = (
        "description",
        "person_display_name",
        "company",
        "expense_date",
        "total_amount",
        "is_registered",
        "is_paid",
    )
    list_filter = ("is_registered", "is_paid")
    search_fields = ("description", "person_name")
    inlines = [ExpenseClaimPaymentInline]
