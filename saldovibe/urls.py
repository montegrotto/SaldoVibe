"""
URL configuration for saldovibe project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("konton/", include("accounts.urls", namespace="accounts")),
    path("", include("auditlog.urls", namespace="auditlog")),
    path("", include("attachments.urls", namespace="attachments")),
    path("", include("supplier_invoices.urls", namespace="supplier_invoices")),
    path("", include("expenses.urls", namespace="expenses")),
    path("", include("invoicing.urls", namespace="invoicing")),
    path("", include("banking.urls", namespace="banking")),
    path("", include("payroll.urls", namespace="payroll")),
    path("", include("vat.urls", namespace="vat")),
    path("", include("fixed_assets.urls", namespace="fixed_assets")),
    path("", include("bookkeeping.urls", namespace="bookkeeping")),
]

if settings.SERVE_STATIC:
    urlpatterns = [
        re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
    ] + urlpatterns

if settings.SERVE_MEDIA:
    urlpatterns = [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ] + urlpatterns
