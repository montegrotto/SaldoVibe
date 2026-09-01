from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("kunder/", views.customer_list, name="customer_list"),
    path("kunder/ny/", views.customer_create, name="customer_create"),
    path("kunder/<int:pk>/redigera/", views.customer_update, name="customer_update"),
    path("artiklar/", views.article_list, name="article_list"),
    path("artiklar/ny/", views.article_create, name="article_create"),
    path("artiklar/<int:pk>/redigera/", views.article_update, name="article_update"),
    path("kundfakturor/", views.invoice_list, name="invoice_list"),
    path("kundfakturor/ny/", views.invoice_create, name="invoice_create"),
    path("kundfakturor/<int:invoice_id>/", views.invoice_detail, name="invoice_detail"),
    path(
        "kundfakturor/<int:invoice_id>/bilagor/lagg-till/",
        views.invoice_attachment_add,
        name="invoice_attachment_add",
    ),
    path(
        "kundfakturor/<int:invoice_id>/bilagor/ta-bort/",
        views.invoice_attachment_remove,
        name="invoice_attachment_remove",
    ),
    path("kundfakturor/<int:invoice_id>/bokfor/", views.invoice_book, name="invoice_book"),
    path(
        "kundfakturor/<int:invoice_id>/registrera-betalning/",
        views.invoice_register_payment,
        name="invoice_register_payment",
    ),
    path(
        "kundfakturor/<int:invoice_id>/kvitta/",
        views.invoice_offset,
        name="invoice_offset",
    ),
    path(
        "kundfakturor/<int:invoice_id>/angra-manuell-betalning/",
        views.invoice_unmark_manually_paid,
        name="invoice_unmark_manually_paid",
    ),
    path("kundfakturor/<int:invoice_id>/kreditera/", views.invoice_credit, name="invoice_credit"),
    path("kundfakturor/<int:invoice_id>/ta-bort/", views.invoice_delete, name="invoice_delete"),
    path("kundfakturor/<int:invoice_id>/utskrift/", views.invoice_print, name="invoice_print"),
    path("kundfakturor/<int:invoice_id>/skicka-epost/", views.invoice_email, name="invoice_email"),
    path(
        "kundfakturor/<int:invoice_id>/paminnelse/skicka-epost/",
        views.invoice_reminder_email,
        name="invoice_reminder_email",
    ),
    path(
        "kundfakturor/<int:invoice_id>/paminnelse/",
        views.invoice_reminder_print,
        name="invoice_reminder_print",
    ),
    path(
        "kundfakturor/<int:invoice_id>/paminnelse/<int:reminder_id>/",
        views.invoice_reminder_reprint,
        name="invoice_reminder_reprint",
    ),
    path(
        "kundfakturor/<int:invoice_id>/peppol/", views.invoice_peppol_xml_download, name="invoice_peppol_xml_download"
    ),
    path("aterkommande-fakturor/", views.recurring_invoice_list, name="recurring_invoice_list"),
    path("aterkommande-fakturor/ny/", views.recurring_invoice_create, name="recurring_invoice_create"),
    path(
        "aterkommande-fakturor/<int:recurring_invoice_id>/",
        views.recurring_invoice_detail,
        name="recurring_invoice_detail",
    ),
    path(
        "aterkommande-fakturor/<int:recurring_invoice_id>/redigera/",
        views.recurring_invoice_update,
        name="recurring_invoice_update",
    ),
    path(
        "aterkommande-fakturor/<int:recurring_invoice_id>/generera/",
        views.recurring_invoice_generate,
        name="recurring_invoice_generate",
    ),
    path(
        "aterkommande-fakturor/<int:recurring_invoice_id>/vaxla-aktiv/",
        views.recurring_invoice_toggle_active,
        name="recurring_invoice_toggle_active",
    ),
    path(
        "aterkommande-fakturor/<int:recurring_invoice_id>/ta-bort/",
        views.recurring_invoice_delete,
        name="recurring_invoice_delete",
    ),
]
