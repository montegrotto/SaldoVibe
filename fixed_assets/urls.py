from django.urls import path

from . import views

app_name = "fixed_assets"

urlpatterns = [
    path("anlaggningstillgangar/", views.asset_list, name="asset_list"),
    path("anlaggningstillgangar/alerts/ack/", views.acknowledge_alerts, name="acknowledge_alerts"),
    path("anlaggningstillgangar/ny/", views.asset_create, name="asset_create"),
    path("anlaggningstillgangar/typer/", views.asset_type_list, name="asset_type_list"),
    path("anlaggningstillgangar/typer/ny/", views.asset_type_create, name="asset_type_create"),
    path("anlaggningstillgangar/typer/<int:pk>/redigera/", views.asset_type_update, name="asset_type_update"),
    path("anlaggningstillgangar/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("anlaggningstillgangar/<int:pk>/redigera/", views.asset_update, name="asset_update"),
    path("anlaggningstillgangar/<int:pk>/radera/", views.asset_delete, name="asset_delete"),
    path("anlaggningstillgangar/<int:pk>/avskriv/", views.asset_run_depreciation, name="asset_run_depreciation"),
    path(
        "anlaggningstillgangar/<int:pk>/nedskriv/",
        views.asset_register_impairment,
        name="asset_register_impairment",
    ),
    path(
        "anlaggningstillgangar/<int:pk>/avskrivning/<int:entry_pk>/korrigera/",
        views.asset_correct_depreciation,
        name="asset_correct_depreciation",
    ),
    path(
        "anlaggningstillgangar/<int:pk>/nedskrivning/<int:entry_pk>/korrigera/",
        views.asset_correct_impairment,
        name="asset_correct_impairment",
    ),
    path("anlaggningstillgangar/<int:pk>/avyttra/", views.asset_dispose, name="asset_dispose"),
]
