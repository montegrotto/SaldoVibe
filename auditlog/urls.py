from django.urls import path

from . import views

app_name = "auditlog"

urlpatterns = [
    path("loggar/", views.audit_log_report, name="report"),
]
