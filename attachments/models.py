import hashlib
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from PIL import Image, ImageDraw, ImageOps


class TransactionAttachment(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manuell"
        EMAIL = "email", "E-post"

    company = models.ForeignKey(
        "bookkeeping.Company",
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="F\u00f6retag",
    )
    file = models.FileField(
        "Fil",
        upload_to="transaction_attachments/%Y/%m/",
        validators=[FileExtensionValidator(allowed_extensions=["pdf", "png", "jpg", "jpeg"])],
    )
    thumbnail = models.ImageField(
        "Miniatyr",
        upload_to="transaction_attachment_thumbs/%Y/%m/",
        null=True,
        blank=True,
    )
    thumbnail_generated_at = models.DateTimeField("Miniatyr skapad", null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Uppladdad av",
    )
    uploaded_at = models.DateTimeField("Uppladdad", auto_now_add=True)
    deleted_at = models.DateTimeField("Borttagen vid", null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_attachments",
        verbose_name="Borttagen av",
    )
    delete_reason = models.CharField("Orsak borttagning", max_length=255, blank=True)
    source = models.CharField("Källa", max_length=20, choices=Source.choices, default=Source.MANUAL)
    source_provider = models.CharField(max_length=20, blank=True)
    source_message_id = models.CharField(max_length=255, blank=True)
    source_attachment_id = models.CharField(max_length=255, blank=True)
    source_email_subject = models.CharField(max_length=255, blank=True)
    content_hash = models.CharField("Innehållshash", max_length=64, blank=True, db_index=True)
    extracted_data = models.JSONField(
        "Föreslagna fält (ReInvGrabber)",
        null=True,
        blank=True,
        help_text="Belopp/moms/leverantör m.m. föreslagna av ReInvGrabber - bara ett förslag, "
        "aldrig bokförd data i sig. Null om integrationen är avstängd eller anropet misslyckades.",
    )

    class Meta:
        db_table = "bookkeeping_transactionattachment"
        ordering = ["-uploaded_at"]
        verbose_name = "Bilaga"
        verbose_name_plural = "Bilagor"

    def __str__(self):
        return self.file.name.split("/")[-1]

    def compute_content_hash(self):
        if not self.file:
            return ""

        digest = hashlib.sha256()
        file = self.file
        # An upload that hasn't reached storage yet is already open, and Django
        # still needs to read it afterwards -- rewind it instead of closing it.
        was_closed = file.closed
        if was_closed:
            file.open("rb")
        else:
            file.seek(0)
        try:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
        finally:
            if was_closed:
                file.close()
            else:
                file.seek(0)
        return digest.hexdigest()

    def save(self, *args, **kwargs):
        # Filled here rather than only in the import path so manual uploads are
        # deduplicated against emailed copies of the same document.
        if self.file and not self.content_hash:
            self.content_hash = self.compute_content_hash()
        super().save(*args, **kwargs)

    @property
    def file_name(self):
        return self.file.name.split("/")[-1]

    @property
    def thumbnail_name(self):
        stem = Path(self.file_name).stem
        return f"{stem}_thumb.jpg"

    def _build_pdf_placeholder_thumbnail(self):
        image = Image.new("RGB", (360, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 20, 330, 340), outline="#d0d7de", width=3)
        draw.rectangle((30, 20, 330, 90), fill="#dc3545")
        draw.text((145, 45), "PDF", fill="white")
        draw.text((45, 130), self.file_name[:30], fill="#495057")
        return image

    def is_legacy_pdf_placeholder_thumbnail(self):
        if not self.thumbnail:
            return False
        try:
            self.thumbnail.open("rb")
            with Image.open(self.thumbnail) as thumb:
                image = thumb.convert("RGB")
                if image.width < 120 or image.height < 120:
                    return False
                red_probe = image.getpixel((40, 40))
                white_probe = image.getpixel((40, 120))
                border_probe = image.getpixel((30, 20))
                return (
                    red_probe[0] > 180
                    and red_probe[1] < 90
                    and red_probe[2] < 90
                    and white_probe[0] > 220
                    and white_probe[1] > 220
                    and white_probe[2] > 220
                    and border_probe[0] > 170
                    and border_probe[1] > 170
                    and border_probe[2] > 170
                )
        except Exception:
            return False
        finally:
            if self.thumbnail and not self.thumbnail.closed:
                self.thumbnail.close()

    def _build_pdf_first_page_thumbnail(self):
        import pypdfium2 as pdfium

        self.file.open("rb")
        pdf_bytes = self.file.read()
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            if len(pdf) == 0:
                return self._build_pdf_placeholder_thumbnail()
            page = pdf[0]
            bitmap = page.render(scale=1.6)
            image = bitmap.to_pil().convert("RGB")
            return image
        finally:
            pdf.close()

    def generate_thumbnail(self):
        if not self.file:
            return False

        lower_name = self.file_name.lower()
        image = None

        try:
            if lower_name.endswith((".png", ".jpg", ".jpeg")):
                self.file.open("rb")
                with Image.open(self.file) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
            elif lower_name.endswith(".pdf"):
                try:
                    image = self._build_pdf_first_page_thumbnail()
                except Exception:
                    image = self._build_pdf_placeholder_thumbnail()
            else:
                return False

            image.thumbnail((360, 360), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
            buffer.seek(0)

            if self.thumbnail:
                self.thumbnail.delete(save=False)
            self.thumbnail.save(self.thumbnail_name, ContentFile(buffer.read()), save=False)
            self.thumbnail_generated_at = timezone.now()
            return True
        finally:
            if self.file and not self.file.closed:
                self.file.close()
