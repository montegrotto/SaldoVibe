from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("bilagor/", views.attachment_list, name="attachment_list"),
    path("bilagor/valj/", views.attachment_picker, name="attachment_picker"),
    path("bilagor/<int:attachment_id>/miniatyr/", views.attachment_thumbnail, name="attachment_thumbnail"),
    path("bilagor/<int:attachment_id>/forhandsvisa/", views.attachment_preview, name="attachment_preview"),
    path("bilagor/<int:attachment_id>/ta-bort/", views.attachment_delete, name="attachment_delete"),
]
