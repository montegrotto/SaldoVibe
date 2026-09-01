from django.contrib import admin

from .models import Article, Customer, Invoice, InvoiceLine


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "org_number", "email", "phone", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("name", "org_number", "email", "phone")


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("article_number", "name", "company", "income_account", "unit_price", "vat_rate", "is_active")
    list_filter = ("company", "vat_rate", "is_active")
    search_fields = ("article_number", "name", "description")


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "ocr_code", "company", "customer", "is_booked", "invoice_date", "due_date")
    list_filter = ("company", "is_booked", "invoice_date", "due_date")
    search_fields = ("invoice_number", "ocr_code", "customer__name", "reference")
    inlines = [InvoiceLineInline]
