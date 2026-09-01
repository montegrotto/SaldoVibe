"""Klient mot ReInvGrabbers extraction-paket, som läser bokförings-/
betalningsfält (belopp, moms, leverantör, OCR-referens, ...) ur en
uppladdad bilaga med lokal OCR. Körs numera i samma process som SaldoVibe
(se requirements.txt) - inte längre som en egen tjänst; källan till
extraction-paketet ligger fortfarande i ett eget repo
(https://github.com/montegrotto/ReInvGrabber), tillsammans med det
fristående Flask+HTML-verktyg som används för att vidareutveckla
extraktionslogiken mot lokala kvitton/fakturor.

Anropet är avsiktligt "best effort": en extraherad summa är bara ett
förslag som användaren ändå kan ändra i formuläret, så ett fel i
extraktionen ska ALDRIG stoppa själva bilageuppladdningen - varje fel
fångas och loggas här, aldrig kastas vidare till anroparen.
extraction.pipeline.process_file fångar redan sina egna fel per fil; detta
skyddsnätet är till för allt oförutsett runt omkring (t.ex. om Tesseract
saknas i körmiljön).
"""

import logging

from django.conf import settings
from extraction.pipeline import process_file

logger = logging.getLogger(__name__)


def extract_fields(file_bytes, file_name, own_company=None):
    """Extraherar fält ur en bilaga, eller returnerar None om integrationen
    är avstängd eller extraktionen misslyckas oväntat - aldrig en
    exception (se modulens docstring för varför)."""
    if not getattr(settings, "REINVGRABBER_ENABLED", True):
        return None
    try:
        return process_file(file_name, file_bytes, own_company)
    except Exception:
        logger.exception("Oväntat fel vid ReInvGrabber-extraktion för %s", file_name)
        return None
