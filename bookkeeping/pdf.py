from io import BytesIO

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.http import content_disposition_header


class PdfRenderError(Exception):
    """Raised by `render_pdf_bytes` when xhtml2pdf is unavailable or fails to render."""


def render_pdf_bytes(template_name, context) -> bytes:
    """Render a template to PDF bytes via xhtml2pdf. Raises PdfRenderError on failure."""
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:
        raise PdfRenderError("PDF-export kräver paketet xhtml2pdf. Installera beroenden och försök igen.") from exc

    html = render_to_string(template_name, context)
    output = BytesIO()
    pdf = pisa.CreatePDF(src=html, dest=output, encoding="utf-8")
    if pdf.err:
        raise PdfRenderError("Kunde inte skapa PDF-rapport.")

    return output.getvalue()


def logo_dimensions_mm(company_icon, max_width=85.0, max_height=18.0):
    """Skala loggan till PDF:ns loggyta med bevarat bildförhållande.

    pisa förvränger bilder som bara får en dimension satt och saknar
    max-width/max-height, så måtten räknas ut här. Returnerar (bredd, höjd)
    som strängar i mm — strängar för att undvika lokaliserat decimalkomma i
    mallen — eller None om filen saknas/inte kan läsas (då visas
    företagsnamnet i stället).
    """
    try:
        from PIL import Image

        with Image.open(company_icon.path) as image:
            width_px, height_px = image.size
    except Exception:
        return None
    height = min(max_height, max_width * height_px / width_px)
    width = height * width_px / height_px
    return f"{width:.1f}", f"{height:.1f}"


def company_logo_size(company):
    """`logo_size`-kontextvärdet för PDF-sidhuvudets logga, eller None för namnfallback."""
    return logo_dimensions_mm(company.company_icon) if company.company_icon else None


def render_pdf_response(template_name, context, filename, disposition="attachment"):
    """Render a template to a PDF download response via xhtml2pdf."""
    try:
        payload = render_pdf_bytes(template_name, context)
    except PdfRenderError as exc:
        return HttpResponse(str(exc), status=500, content_type="text/plain; charset=utf-8")

    response = HttpResponse(payload, content_type="application/pdf")
    # content_disposition_header RFC 5987-kodar icke-ASCII-filnamn (t.ex. "balansräkning")
    # som filename*=utf-8'' — utan den faller webbläsaren tillbaka på "download.pdf".
    response["Content-Disposition"] = content_disposition_header(disposition == "attachment", filename)
    return response
