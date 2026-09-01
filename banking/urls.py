from django.urls import path

from . import views

app_name = "banking"

urlpatterns = [
    path("banking/kallor/", views.account_list, name="account_list"),
    path("banking/kallor/ny/", views.account_create, name="account_create"),
    path("banking/kallor/<int:pk>/redigera/", views.account_update, name="account_update"),
    path("banking/transaktioner/", views.transaction_list, name="transaction_list"),
    path("banking/transaktioner/import/", views.import_transactions, name="import_transactions"),
    path("banking/transaktioner/manuell/", views.create_manual_transaction, name="create_manual_transaction"),
    path(
        "banking/transaktioner/<int:transaction_id>/snabbbokfor/",
        views.quick_book_transaction,
        name="quick_book_transaction",
    ),
    path("banking/transaktioner/<int:transaction_id>/bokfor/", views.book_transaction, name="book_transaction"),
    path("banking/transaktioner/<int:transaction_id>/ta-bort/", views.delete_transaction, name="delete_transaction"),
    path(
        "banking/betalningar/<int:transaction_id>/angra/",
        views.payment_undo_confirm,
        name="payment_undo_confirm",
    ),
    path(
        "banking/betalningar/<int:transaction_id>/angra/utfor/",
        views.payment_undo,
        name="payment_undo",
    ),
]
