from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("bilagor/", views.attachment_list, name="attachment_list"),
    path("bilagor/valj/", views.attachment_picker, name="attachment_picker"),
    path("bilagor/uppladdningstoken/", views.attachment_upload_token, name="attachment_upload_token"),
    path("bilagor/api/ladda-upp/", views.attachment_api_upload, name="attachment_api_upload"),
    # Without the slash too: APPEND_SLASH answers a POST with a 301 that clients
    # follow as a body-less GET, so a hand-typed URL would silently drop the file.
    path("bilagor/api/ladda-upp", views.attachment_api_upload),
    path("bilagor/<int:attachment_id>/miniatyr/", views.attachment_thumbnail, name="attachment_thumbnail"),
    path("bilagor/<int:attachment_id>/forhandsvisa/", views.attachment_preview, name="attachment_preview"),
    path("bilagor/<int:attachment_id>/ta-bort/", views.attachment_delete, name="attachment_delete"),
]
