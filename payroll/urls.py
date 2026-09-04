from django.urls import path

from . import views

app_name = "payroll"

urlpatterns = [
    path("anstallda/", views.employee_list, name="employee_list"),
    path("anstallda/ny/", views.employee_create, name="employee_create"),
    path("anstallda/<int:pk>/redigera/", views.employee_update, name="employee_update"),
    path("lon/lonekorningar/", views.payroll_run_list, name="payroll_run_list"),
    path("lon/lonekorningar/ny/", views.payroll_run_create, name="payroll_run_create"),
    path("lon/lonekorningar/<int:payroll_run_id>/", views.payroll_run_detail, name="payroll_run_detail"),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/avsluta/",
        views.payroll_run_finish,
        name="payroll_run_finish",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/lagg-till-anstalld/",
        views.payroll_run_add_employee,
        name="payroll_run_add_employee",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/ta-bort-lonepost/<int:salary_record_id>/",
        views.payroll_run_remove_employee,
        name="payroll_run_remove_employee",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/lonespecifikation/<int:salary_record_id>/",
        views.salary_report_print,
        name="salary_report_print",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/lonespec/<int:salary_record_id>/skicka-epost/",
        views.salary_report_email,
        name="salary_report_email",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/lonepost/<int:salary_record_id>/redigera/",
        views.salary_record_update,
        name="salary_record_update",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/skatteverket-bevispaket/",
        views.skatteverket_report_evidence_download,
        name="skatteverket_report_evidence_download",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/skatteverket-agi-xml/",
        views.skatteverket_agi_xml_download,
        name="skatteverket_agi_xml_download",
    ),
    path(
        "lon/lonekorningar/<int:payroll_run_id>/skatteverket-rapporterad/",
        views.skatteverket_report_mark_reported,
        name="skatteverket_report_mark_reported",
    ),
]
