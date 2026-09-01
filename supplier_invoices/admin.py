from django.contrib import admin

from .models import Supplier, SupplierInvoice


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "org_number", "email", "phone", "bankgiro", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("name", "org_number", "email", "phone", "bankgiro")
    ordering = ("name",)


@admin.register(SupplierInvoice)
class SupplierInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "ocr_code",
        "supplier",
        "company",
        "invoice_date",
        "due_date",
        "total_amount",
        "vat_amount",
        "is_registered",
        "is_paid",
    )
    list_filter = ("company", "is_registered", "is_paid", "invoice_date", "due_date")
    search_fields = ("invoice_number", "supplier__name", "supplier_name")
    ordering = ("-invoice_date", "-created_at")
    filter_horizontal = ("attachments",)
