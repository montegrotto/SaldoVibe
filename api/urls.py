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
    path("utlagg/<int:pk>/bokfor/", views.expense_register, name="expense_register"),
    path("utlagg/<int:pk>/betalning/", views.expense_payment, name="expense_payment"),
    path("korrapporter/", views.mileage_reports, name="mileage_reports"),
    path("leverantorsfakturor/", views.supplier_invoices, name="supplier_invoices"),
    path("leverantorsfakturor/<int:pk>/", views.supplier_invoice_detail, name="supplier_invoice_detail"),
    path("leverantorsfakturor/<int:pk>/bokfor/", views.supplier_invoice_register, name="supplier_invoice_register"),
    path("leverantorsfakturor/<int:pk>/betalning/", views.supplier_invoice_payment, name="supplier_invoice_payment"),
    path("kundfakturor/", views.customer_invoices, name="customer_invoices"),
    path("kundfakturor/<int:pk>/", views.customer_invoice_detail, name="customer_invoice_detail"),
    path("kundfakturor/<int:pk>/betalning/", views.customer_invoice_payment, name="customer_invoice_payment"),
]
