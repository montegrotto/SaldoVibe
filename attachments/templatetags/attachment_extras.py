from django import template
from django.urls import reverse

register = template.Library()


@register.filter
def attachment_panel_data(attachments):
    """Serialize attachments for the shared split-panel viewer (see attachments/_attachment_panel.html)."""

    data = []
    for attachment in attachments:
        file_name = attachment.file_name
        data.append(
            {
                "id": attachment.id,
                "file_name": file_name,
                "file_url": reverse("attachments:attachment_preview", args=[attachment.id]),
                "is_pdf": file_name.lower().endswith(".pdf"),
            }
        )
    return data
