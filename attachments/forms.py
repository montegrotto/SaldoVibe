from django import forms

from .models import TransactionAttachment


class TransactionAttachmentForm(forms.ModelForm):
    class Meta:
        model = TransactionAttachment
        fields = ("file",)
        widgets = {
            "file": forms.ClearableFileInput(
                attrs={
                    "accept": "image/jpeg,image/png,application/pdf",
                    "hidden": True,
                    "onchange": "if(this.files.length)this.form.requestSubmit()",
                }
            ),
        }

    MAX_FILE_SIZE = 25 * 1024 * 1024

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError("Filen är för stor (max 25 MB).")
        allowed_types = {"application/pdf", "image/png", "image/jpeg"}
        content_type = getattr(file, "content_type", None)
        if content_type and content_type not in allowed_types:
            raise forms.ValidationError("Endast PDF, PNG eller JPEG \u00e4r till\u00e5tet.")
        return file
