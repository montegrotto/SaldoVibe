from django.urls import path

from . import views

app_name = "vat"

urlpatterns = [
    path("moms/", views.vat_report, name="report"),
    path("moms/export/", views.vat_export_skatteverket, name="export_skatteverket"),
    path("moms/stang-period/", views.vat_close_period, name="close_period"),
    path("moms/falt/<str:field_code>/", views.vat_field_transactions, name="field_transactions"),
]
