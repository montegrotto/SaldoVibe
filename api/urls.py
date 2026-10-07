from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("auth/login/", views.login, name="login"),
    path("auth/logout/", views.logout, name="logout"),
    path("me/", views.me, name="me"),
    path("oversikt/", views.overview, name="overview"),
    path("formulardata/", views.form_choices, name="form_choices"),
    path("resultatrakning/", views.income_statement, name="income_statement"),
    path("balansrakning/", views.balance_sheet, name="balance_sheet"),
    path("bilagor/", views.attachments, name="attachments"),
    path("bilagor/<int:pk>/fil/", views.attachment_file, name="attachment_file"),
    path("bilagor/<int:pk>/miniatyr/", views.attachment_thumbnail, name="attachment_thumbnail"),
    path("utlagg/", views.expenses, name="expenses"),
    path("utlagg/<int:pk>/", views.expense_detail, name="expense_detail"),
    path("korrapporter/", views.mileage_reports, name="mileage_reports"),
    path("leverantorsfakturor/", views.supplier_invoices, name="supplier_invoices"),
    path("leverantorsfakturor/<int:pk>/", views.supplier_invoice_detail, name="supplier_invoice_detail"),
    path("kundfakturor/", views.customer_invoices, name="customer_invoices"),
    path("kundfakturor/<int:pk>/", views.customer_invoice_detail, name="customer_invoice_detail"),
]
