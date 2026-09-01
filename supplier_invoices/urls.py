from django.urls import path

from . import views

app_name = "supplier_invoices"

urlpatterns = [
    path("leverantorer/", views.supplier_list, name="supplier_list"),
    path("leverantorer/ny/", views.supplier_create, name="supplier_create"),
    path("leverantorer/<int:pk>/redigera/", views.supplier_update, name="supplier_update"),
    path("leverantorsfakturor/", views.invoice_list, name="invoice_list"),
    path("leverantorsfakturor/ny/", views.invoice_create, name="invoice_create"),
    path(
        "leverantorsfakturor/senaste-faktura/",
        views.supplier_last_invoice,
        name="supplier_last_invoice",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/",
        views.invoice_detail,
        name="invoice_detail",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/qr.svg",
        views.invoice_qr_svg,
        name="invoice_qr_svg",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/bilagor/lagg-till/",
        views.invoice_attachment_add,
        name="invoice_attachment_add",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/bilagor/ta-bort/",
        views.invoice_attachment_remove,
        name="invoice_attachment_remove",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/registrera/",
        views.invoice_register,
        name="invoice_register",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/registrera-betalning/",
        views.invoice_register_payment,
        name="invoice_register_payment",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/kvitta/",
        views.invoice_offset,
        name="invoice_offset",
    ),
    path(
        "leverantorsfakturor/<int:invoice_id>/angra-manuell-betalning/",
        views.invoice_unmark_manually_paid,
        name="invoice_unmark_manually_paid",
    ),
]
