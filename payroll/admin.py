from django.contrib import admin

from .models import Employee, EmployeeDefaultAdjustment, PayrollRun, SalaryPaymentReminder, SalaryRecord


class EmployeeDefaultAdjustmentInline(admin.TabularInline):
    model = EmployeeDefaultAdjustment
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "masked_personal_identity_number",
        "company",
        "monthly_salary",
        "tax_table_number",
        "tax_table_column",
        "is_active",
    )
    list_filter = ("company", "is_active")
    # personnumret är krypterat at rest — textsökning på kolumnen träffar bara ciphertext
    search_fields = ("first_name", "last_name")
    inlines = [EmployeeDefaultAdjustmentInline]

    @admin.display(description="Personnummer", ordering="personal_identity_number")
    def masked_personal_identity_number(self, obj):
        return obj.masked_personal_identity_number


class SalaryRecordInline(admin.TabularInline):
    model = SalaryRecord
    extra = 0


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "period_year",
        "period_month",
        "payment_date",
        "is_finished",
        "is_reported_to_skatteverket",
    )
    list_filter = ("company", "period_year", "period_month", "is_finished", "is_reported_to_skatteverket")
    inlines = [SalaryRecordInline]


@admin.register(SalaryRecord)
class SalaryRecordAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "payroll_run",
        "gross_salary",
        "tax_table_number",
        "tax_table_column",
        "tax_calculation_source",
        "preliminary_tax_amount",
        "employer_contribution_amount",
        "net_salary",
    )
    list_filter = ("payroll_run__company", "payroll_run__period_year", "payroll_run__period_month")


@admin.register(SalaryPaymentReminder)
class SalaryPaymentReminderAdmin(admin.ModelAdmin):
    list_display = ("company", "payroll_run", "employee", "due_date", "amount", "is_completed")
    list_filter = ("company", "due_date", "is_completed")
    search_fields = ("employee__first_name", "employee__last_name", "description")
