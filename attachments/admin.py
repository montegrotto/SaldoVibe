from django.contrib import admin

from .models import TransactionAttachment


@admin.register(TransactionAttachment)
class TransactionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("file", "company", "uploaded_by", "uploaded_at", "deleted_at", "deleted_by")
    list_filter = ("company", "uploaded_at", "deleted_at")
    search_fields = ("file",)
