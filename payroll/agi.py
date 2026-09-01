"""Arbetsgivardeklaration på individnivå (AGI) - XML file generation.

Builds the XML file format Skatteverket's technical specification defines for
submitting employer declarations at individual level ("eSKD" instance schema
for area "Arbetsgivardeklaration"). This module only generates the file - the
employer still uploads it themselves via Skatteverket's e-service
(https://www.skatteverket.se/foretag/arbetsgivare/lamnaarbetsgivardeklaration).
There is no API submission here.

Verified against Skatteverket's published technical description (teknisk
beskrivning 1.1.18.2): the instance/component XSD schemas
(arbetsgivardeklaration_1.1.xsd, arbetsgivardeklaration_component_1.1.xsd) and
their official example files (Exempelfil_01_Vanliga_lontagare).
"""

import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

NS_INSTANCE = "http://xmls.skatteverket.se/se/skatteverket/da/instans/schema/1.1"
NS_COMPONENT = "http://xmls.skatteverket.se/se/skatteverket/da/komponent/schema/1.1"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = (
    "http://xmls.skatteverket.se/se/skatteverket/da/instans/schema/1.1 "
    "http://xmls.skatteverket.se/se/skatteverket/da/arbetsgivardeklaration/arbetsgivardeklaration_1.1.xsd"
)

ET.register_namespace("", NS_INSTANCE)
ET.register_namespace("agd", NS_COMPONENT)
ET.register_namespace("xsi", NS_XSI)


def _agd(tag):
    return f"{{{NS_COMPONENT}}}{tag}"


class AGIValidationError(Exception):
    """Raised with a list of Swedish, user-facing messages describing what's missing."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def normalize_org_number_identitet(value):
    """Convert a Swedish organisationsnummer to Skatteverket's 12-digit IDENTITET
    form (century prefix "16" + the 10-digit number). Returns None if it can't
    be normalized to exactly 10 digits."""

    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 10:
        return f"16{digits}"
    if len(digits) == 12 and digits.startswith("16"):
        return digits
    return None


def normalize_personnummer_identitet(value, *, reference_date=None):
    """Convert a personnummer to Skatteverket's 12-digit IDENTITET form
    (century + YYMMDD + 4 digits). Accepts either the legacy 10-digit form
    (no century) or an already-12-digit value. Century for the 10-digit form
    is inferred the standard way: assume the person isn't over 100 - if the
    2-digit year is greater than this year's 2-digit year, assume 19xx,
    otherwise 20xx. Returns None if it can't be normalized to 10 or 12 digits."""

    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 12:
        return digits
    if len(digits) == 10:
        reference_date = reference_date or timezone.localdate()
        year_2digit = int(digits[:2])
        current_2digit = reference_date.year % 100
        century = 1900 if year_2digit > current_2digit else 2000
        return f"{century + year_2digit:04d}{digits[2:]}"
    return None


def _whole_kronor(value):
    return int((value or Decimal("0.00")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_payroll_run_for_agi(payroll_run, *, sender_name, sender_email, sender_phone):
    """Check every field the AGI file format requires. Raises AGIValidationError
    with all problems found (not just the first one)."""

    errors = []
    company = payroll_run.company

    if normalize_org_number_identitet(company.org_number) is None:
        errors.append("Företagets organisationsnummer saknas eller är ogiltigt (Företagsinställningar).")

    if not (sender_name or "").strip():
        errors.append("Kontaktpersonens namn saknas (den inloggade användarens namn används som teknisk kontakt).")
    if not (sender_email or "").strip():
        errors.append("Kontaktpersonens e-postadress saknas.")
    if not (sender_phone or "").strip():
        errors.append("Företagets telefonnummer saknas (Företagsinställningar) - används som teknisk kontakt.")

    records = list(
        payroll_run.salary_records.select_related("employee").order_by("employee__personal_identity_number_hash", "id")
    )
    if not records:
        errors.append("Lönekörningen saknar löneposter.")

    for record in records:
        if normalize_personnummer_identitet(record.employee.personal_identity_number) is None:
            errors.append(
                f"Personnumret för {record.employee} ({record.employee.personal_identity_number!r}) "
                "är inte 10 eller 12 siffror och kan inte tolkas."
            )

    if errors:
        raise AGIValidationError(errors)


def generate_agi_xml(payroll_run, *, sender_name, sender_email, sender_phone):
    """Return the AGI XML file for one payroll run (one Redovisningsperiod) as bytes.

    Raises AGIValidationError if mandatory data is missing. Caller is expected
    to catch that and show the messages to the user before this is ever
    called from a view.
    """
    validate_payroll_run_for_agi(
        payroll_run, sender_name=sender_name, sender_email=sender_email, sender_phone=sender_phone
    )

    company = payroll_run.company
    employer_id = normalize_org_number_identitet(company.org_number)
    period = f"{payroll_run.period_year}{payroll_run.period_month:02d}"

    records = list(
        payroll_run.salary_records.select_related("employee").order_by("employee__personal_identity_number_hash", "id")
    )
    # Summeras från de avrundade raderna så att HU-blocket stämmer mot IU-blocken krona för krona.
    totals = {
        "total_tax": sum(_whole_kronor(r.preliminary_tax_amount) for r in records),
        "total_employer": sum(_whole_kronor(r.employer_contribution_amount) for r in records),
    }

    root = ET.Element(f"{{{NS_INSTANCE}}}Skatteverket")
    root.set("omrade", "Arbetsgivardeklaration")
    root.set(f"{{{NS_XSI}}}schemaLocation", SCHEMA_LOCATION)

    sender = ET.SubElement(root, _agd("Avsandare"))
    ET.SubElement(sender, _agd("Programnamn")).text = "SaldoVibe"
    ET.SubElement(sender, _agd("Organisationsnummer")).text = employer_id
    technical_contact = ET.SubElement(sender, _agd("TekniskKontaktperson"))
    ET.SubElement(technical_contact, _agd("Namn")).text = sender_name.strip()
    ET.SubElement(technical_contact, _agd("Telefon")).text = sender_phone.strip()
    ET.SubElement(technical_contact, _agd("Epostadress")).text = sender_email.strip()
    ET.SubElement(sender, _agd("Skapad")).text = timezone.now().replace(microsecond=0).isoformat()

    common = ET.SubElement(root, _agd("Blankettgemensamt"))
    employer = ET.SubElement(common, _agd("Arbetsgivare"))
    ET.SubElement(employer, _agd("AgRegistreradId")).text = employer_id
    contact = ET.SubElement(employer, _agd("Kontaktperson"))
    ET.SubElement(contact, _agd("Namn")).text = sender_name.strip()
    ET.SubElement(contact, _agd("Telefon")).text = sender_phone.strip()
    ET.SubElement(contact, _agd("Epostadress")).text = sender_email.strip()

    def new_blankett():
        blankett = ET.SubElement(root, _agd("Blankett"))
        arende = ET.SubElement(blankett, _agd("Arendeinformation"))
        ET.SubElement(arende, _agd("Arendeagare")).text = employer_id
        ET.SubElement(arende, _agd("Period")).text = period
        content = ET.SubElement(blankett, _agd("Blankettinnehall"))
        return content

    hu_content = new_blankett()
    hu = ET.SubElement(hu_content, _agd("HU"))
    hu_employer = ET.SubElement(hu, _agd("ArbetsgivareHUGROUP"))
    ET.SubElement(hu_employer, _agd("AgRegistreradId"), faltkod="201").text = employer_id
    ET.SubElement(hu, _agd("RedovisningsPeriod"), faltkod="006").text = period
    ET.SubElement(hu, _agd("SummaArbAvgSlf"), faltkod="487").text = str(totals["total_employer"])
    ET.SubElement(hu, _agd("SummaSkatteavdr"), faltkod="497").text = str(totals["total_tax"])

    for index, record in enumerate(records, start=1):
        iu_content = new_blankett()
        iu = ET.SubElement(iu_content, _agd("IU"))
        iu_employer = ET.SubElement(iu, _agd("ArbetsgivareIUGROUP"))
        ET.SubElement(iu_employer, _agd("AgRegistreradId"), faltkod="201").text = employer_id
        payee = ET.SubElement(iu, _agd("BetalningsmottagareIUGROUP"))
        payee_id_choice = ET.SubElement(payee, _agd("BetalningsmottagareIDChoice"))
        ET.SubElement(
            payee_id_choice, _agd("BetalningsmottagarId"), faltkod="215"
        ).text = normalize_personnummer_identitet(record.employee.personal_identity_number)
        ET.SubElement(iu, _agd("RedovisningsPeriod"), faltkod="006").text = period
        ET.SubElement(iu, _agd("Specifikationsnummer"), faltkod="570").text = f"{index:03d}"
        ET.SubElement(iu, _agd("KontantErsattningUlagAG"), faltkod="011").text = str(
            _whole_kronor(record.taxable_salary)
        )
        ET.SubElement(iu, _agd("AvdrPrelSkatt"), faltkod="001").text = str(_whole_kronor(record.preliminary_tax_amount))

    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="UTF-8")
