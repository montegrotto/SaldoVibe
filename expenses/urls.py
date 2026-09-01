from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("utlagg/", views.expense_list, name="expense_list"),
    path("utlagg/nytt/", views.expense_create, name="expense_create"),
    path("utlagg/<int:claim_id>/", views.expense_detail, name="expense_detail"),
    path(
        "utlagg/<int:claim_id>/bilagor/lagg-till/",
        views.expense_attachment_add,
        name="expense_attachment_add",
    ),
    path(
        "utlagg/<int:claim_id>/bilagor/ta-bort/",
        views.expense_attachment_remove,
        name="expense_attachment_remove",
    ),
    path("utlagg/<int:claim_id>/registrera/", views.expense_register, name="expense_register"),
    path(
        "utlagg/<int:claim_id>/registrera-betalning/",
        views.expense_register_payment,
        name="expense_register_payment",
    ),
    path(
        "utlagg/<int:claim_id>/angra-manuell-betalning/",
        views.expense_unmark_manually_paid,
        name="expense_unmark_manually_paid",
    ),
    path("utlagg/<int:claim_id>/ta-bort/", views.expense_delete, name="expense_delete"),
]
