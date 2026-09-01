"""Peppol BIS Billing 3.0 (UBL Invoice, EN 16931) XML export.

Generates the XML document a Swedish supplier needs to send a customer invoice
to public-sector buyers as a structured e-invoice, per lagen (2018:1277) om
elektroniska fakturor till följd av offentlig upphandling. This module only
builds and validates the XML file - it does not transmit it. Delivery to the
recipient's Peppol Access Point still requires either the customer's own AP
portal (manual upload of the generated file) or, in a later step, an API
integration with a commercial AP provider.

Reference: https://docs.peppol.eu/poacc/billing/3.0/ (OpenPeppol BIS Billing 3.0)
Participant identifier scheme "0007" = Swedish organisationsnummer, confirmed
against the live Peppol Directory (e.g. Skatteverket is listed as 0007:2021005448).
"""

import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

SE_ORGNR_SCHEME = "0007"

ET.register_namespace("", NS_INVOICE)
ET.register_namespace("cac", NS_CAC)
ET.register_namespace("cbc", NS_CBC)


def _cbc(tag):
    return f"{{{NS_CBC}}}{tag}"


def _cac(tag):
    return f"{{{NS_CAC}}}{tag}"


# UN/ECE Recommendation 20 unit codes for the unit abbreviations actually used
# in Swedish invoices. Falls back to "C62" (generic "one"/unit) for anything
# not listed here - still valid, just less specific.
UNIT_CODE_MAP = {
    "st": "H87",
    "styck": "H87",
    "tim": "HUR",
    "timme": "HUR",
    "timmar": "HUR",
    "h": "HUR",
    "dag": "DAY",
    "dagar": "DAY",
    "vecka": "WEE",
    "veckor": "WEE",
    "mån": "MON",
    "manad": "MON",
    "månad": "MON",
    "månader": "MON",
    "år": "ANN",
    "kg": "KGM",
    "g": "GRM",
    "ton": "TNE",
    "m": "MTR",
    "cm": "CMT",
    "m2": "MTK",
    "m3": "MTQ",
    "l": "LTR",
    "km": "KMT",
    "%": "P1",
}
DEFAULT_UNIT_CODE = "C62"


def resolve_unit_code(unit):
    return UNIT_CODE_MAP.get((unit or "").strip().lower(), DEFAULT_UNIT_CODE)


class PeppolValidationError(Exception):
    """Raised with a list of Swedish, user-facing messages describing what's missing."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _clean_org_number(org_number):
    return "".join(ch for ch in (org_number or "") if ch.isdigit())


def validate_invoice_for_peppol(invoice):
    """Check every field the Peppol BIS Billing 3.0 profile requires. Raises
    PeppolValidationError with all problems found (not just the first one),
    so a user can fix everything in one pass instead of one error at a time."""

    errors = []
    company = invoice.company
    customer = invoice.customer

    if not (company.vat_number or "").strip():
        errors.append("Företagets momsregistreringsnummer saknas (Företagsinställningar).")
    if not _clean_org_number(company.org_number):
        errors.append("Företagets organisationsnummer saknas eller är ogiltigt (Företagsinställningar).")
    if not (company.address or "").strip():
        errors.append("Företagets adress saknas (Företagsinställningar).")
    if not (company.postal_code or "").strip():
        errors.append("Företagets postnummer saknas (Företagsinställningar).")
    if not (company.city or "").strip():
        errors.append("Företagets ort saknas (Företagsinställningar).")
    if not (company.country_code or "").strip():
        errors.append("Företagets landskod saknas (Företagsinställningar).")

    if not _clean_org_number(customer.org_number):
        errors.append(f"Kundens ({customer.name}) organisationsnummer saknas eller är ogiltigt.")
    if not (customer.address or "").strip():
        errors.append(f"Kundens ({customer.name}) adress saknas.")
    if not (customer.postal_code or "").strip():
        errors.append(f"Kundens ({customer.name}) postnummer saknas.")
    if not (customer.city or "").strip():
        errors.append(f"Kundens ({customer.name}) ort saknas.")
    if not (customer.country_code or "").strip():
        errors.append(f"Kundens ({customer.name}) landskod saknas.")

    if not (invoice.reference or "").strip():
        errors.append(
            "Er referens saknas på fakturan. Peppol-fakturor till offentlig sektor måste "
            "innehålla köparens referens (t.ex. den märkning/kod beställaren angav vid beställning)."
        )
    if not invoice.item_lines:
        errors.append("Fakturan saknar artikelrader.")

    if errors:
        raise PeppolValidationError(errors)


def _amount(value):
    return (value or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _vat_category_for_rate(invoice, rate):
    if invoice.reverse_charge:
        return "AE"
    if (rate or Decimal("0.00")) == Decimal("0.00"):
        return "Z"
    return "S"


def _party_xml(parent_tag_parent, party, *, org_number, name, vat_number, address, postal_code, city, country_code):
    party_el = ET.SubElement(parent_tag_parent, _cac("Party"))
    org_digits = _clean_org_number(org_number)
    if org_digits:
        endpoint = ET.SubElement(party_el, _cbc("EndpointID"))
        endpoint.set("schemeID", SE_ORGNR_SCHEME)
        endpoint.text = org_digits

        identification = ET.SubElement(party_el, _cac("PartyIdentification"))
        id_el = ET.SubElement(identification, _cbc("ID"))
        id_el.set("schemeID", SE_ORGNR_SCHEME)
        id_el.text = org_digits

    party_name = ET.SubElement(party_el, _cac("PartyName"))
    ET.SubElement(party_name, _cbc("Name")).text = name

    postal = ET.SubElement(party_el, _cac("PostalAddress"))
    ET.SubElement(postal, _cbc("StreetName")).text = address
    ET.SubElement(postal, _cbc("CityName")).text = city
    ET.SubElement(postal, _cbc("PostalZone")).text = postal_code
    country = ET.SubElement(postal, _cac("Country"))
    ET.SubElement(country, _cbc("IdentificationCode")).text = (country_code or "SE").upper()

    if (vat_number or "").strip():
        tax_scheme_wrap = ET.SubElement(party_el, _cac("PartyTaxScheme"))
        ET.SubElement(tax_scheme_wrap, _cbc("CompanyID")).text = vat_number.strip()
        tax_scheme = ET.SubElement(tax_scheme_wrap, _cac("TaxScheme"))
        ET.SubElement(tax_scheme, _cbc("ID")).text = "VAT"

    legal_entity = ET.SubElement(party_el, _cac("PartyLegalEntity"))
    ET.SubElement(legal_entity, _cbc("RegistrationName")).text = name
    if org_digits:
        company_id = ET.SubElement(legal_entity, _cbc("CompanyID"))
        company_id.set("schemeID", SE_ORGNR_SCHEME)
        company_id.text = org_digits

    return party_el


def generate_peppol_invoice_xml(invoice):
    """Return the Peppol BIS Billing 3.0 UBL Invoice XML for `invoice` as bytes.

    Raises PeppolValidationError if mandatory data is missing. Caller is
    expected to catch that and show the messages to the user before this
    is ever called from a view.
    """
    validate_invoice_for_peppol(invoice)

    company = invoice.company
    customer = invoice.customer
    currency = invoice.currency or "SEK"

    root = ET.Element(f"{{{NS_INVOICE}}}Invoice")
    ET.SubElement(
        root, _cbc("CustomizationID")
    ).text = "urn:cen.eu:en16931:2017#conformant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
    ET.SubElement(root, _cbc("ProfileID")).text = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
    ET.SubElement(root, _cbc("ID")).text = invoice.invoice_number
    ET.SubElement(root, _cbc("IssueDate")).text = invoice.invoice_date.isoformat()
    if invoice.due_date:
        ET.SubElement(root, _cbc("DueDate")).text = invoice.due_date.isoformat()
    ET.SubElement(root, _cbc("InvoiceTypeCode")).text = "381" if invoice.is_credit_invoice else "380"
    if invoice.notes:
        ET.SubElement(root, _cbc("Note")).text = invoice.notes
    ET.SubElement(root, _cbc("DocumentCurrencyCode")).text = currency
    ET.SubElement(root, _cbc("BuyerReference")).text = invoice.reference.strip()

    supplier_wrap = ET.SubElement(root, _cac("AccountingSupplierParty"))
    _party_xml(
        supplier_wrap,
        company,
        org_number=company.org_number,
        name=company.name,
        vat_number=company.vat_number,
        address=company.address,
        postal_code=company.postal_code,
        city=company.city,
        country_code=company.country_code,
    )

    customer_wrap = ET.SubElement(root, _cac("AccountingCustomerParty"))
    _party_xml(
        customer_wrap,
        customer,
        org_number=customer.org_number,
        name=customer.name,
        vat_number=customer.vat_number,
        address=customer.address,
        postal_code=customer.postal_code,
        city=customer.city,
        country_code=customer.country_code,
    )

    if invoice.delivery_date:
        delivery = ET.SubElement(root, _cac("Delivery"))
        ET.SubElement(delivery, _cbc("ActualDeliveryDate")).text = invoice.delivery_date.isoformat()

    payment_means = ET.SubElement(root, _cac("PaymentMeans"))
    ET.SubElement(payment_means, _cbc("PaymentMeansCode")).text = "30"
    payment_id = (invoice.ocr_code or invoice.invoice_number or "").strip()
    if payment_id:
        ET.SubElement(payment_means, _cbc("PaymentID")).text = payment_id
    bank_account_number = (company.bankgiro or company.plusgiro or "").strip()
    if bank_account_number:
        financial_account = ET.SubElement(payment_means, _cac("PayeeFinancialAccount"))
        ET.SubElement(financial_account, _cbc("ID")).text = bank_account_number

    payment_terms = ET.SubElement(root, _cac("PaymentTerms"))
    ET.SubElement(payment_terms, _cbc("Note")).text = f"Betalningsvillkor {invoice.payment_terms_days} dagar"

    total_vat = _amount(invoice.vat_amount)
    subtotal_ex_vat = _amount(invoice.subtotal_ex_vat)
    total_amount = _amount(invoice.total_amount)

    tax_total = ET.SubElement(root, _cac("TaxTotal"))
    tax_amount_el = ET.SubElement(tax_total, _cbc("TaxAmount"))
    tax_amount_el.set("currencyID", currency)
    tax_amount_el.text = str(total_vat)

    if invoice.reverse_charge:
        subtotal = ET.SubElement(tax_total, _cac("TaxSubtotal"))
        taxable_el = ET.SubElement(subtotal, _cbc("TaxableAmount"))
        taxable_el.set("currencyID", currency)
        taxable_el.text = str(subtotal_ex_vat)
        vat_el = ET.SubElement(subtotal, _cbc("TaxAmount"))
        vat_el.set("currencyID", currency)
        vat_el.text = str(Decimal("0.00"))
        category = ET.SubElement(subtotal, _cac("TaxCategory"))
        ET.SubElement(category, _cbc("ID")).text = "AE"
        ET.SubElement(category, _cbc("Percent")).text = "0"
        ET.SubElement(category, _cbc("TaxExemptionReason")).text = "Omvänd betalningsskyldighet / Reverse charge"
        tax_scheme = ET.SubElement(category, _cac("TaxScheme"))
        ET.SubElement(tax_scheme, _cbc("ID")).text = "VAT"
    else:
        for group in invoice.vat_summary:
            subtotal = ET.SubElement(tax_total, _cac("TaxSubtotal"))
            taxable_el = ET.SubElement(subtotal, _cbc("TaxableAmount"))
            taxable_el.set("currencyID", currency)
            taxable_el.text = str(_amount(group["base"]))
            vat_el = ET.SubElement(subtotal, _cbc("TaxAmount"))
            vat_el.set("currencyID", currency)
            vat_el.text = str(_amount(group["vat"]))
            category = ET.SubElement(subtotal, _cac("TaxCategory"))
            ET.SubElement(category, _cbc("ID")).text = _vat_category_for_rate(invoice, group["rate"])
            ET.SubElement(category, _cbc("Percent")).text = str(group["rate"])
            tax_scheme = ET.SubElement(category, _cac("TaxScheme"))
            ET.SubElement(tax_scheme, _cbc("ID")).text = "VAT"

    monetary_total = ET.SubElement(root, _cac("LegalMonetaryTotal"))
    for tag, value in (
        ("LineExtensionAmount", subtotal_ex_vat),
        ("TaxExclusiveAmount", subtotal_ex_vat),
        ("TaxInclusiveAmount", total_amount),
        ("PayableAmount", total_amount),
    ):
        el = ET.SubElement(monetary_total, _cbc(tag))
        el.set("currencyID", currency)
        el.text = str(value)

    for index, line in enumerate(invoice.item_lines, start=1):
        line_el = ET.SubElement(root, _cac("InvoiceLine"))
        ET.SubElement(line_el, _cbc("ID")).text = str(index)
        quantity_el = ET.SubElement(line_el, _cbc("InvoicedQuantity"))
        quantity_el.set("unitCode", resolve_unit_code(line.unit))
        quantity_el.text = str(line.quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        line_amount_el = ET.SubElement(line_el, _cbc("LineExtensionAmount"))
        line_amount_el.set("currencyID", currency)
        line_amount_el.text = str(_amount(line.line_total_ex_vat))

        item = ET.SubElement(line_el, _cac("Item"))
        ET.SubElement(item, _cbc("Name")).text = line.description
        classified_category = ET.SubElement(item, _cac("ClassifiedTaxCategory"))
        ET.SubElement(classified_category, _cbc("ID")).text = _vat_category_for_rate(invoice, line.vat_rate)
        ET.SubElement(classified_category, _cbc("Percent")).text = str(line.vat_rate)
        line_tax_scheme = ET.SubElement(classified_category, _cac("TaxScheme"))
        ET.SubElement(line_tax_scheme, _cbc("ID")).text = "VAT"

        price = ET.SubElement(line_el, _cac("Price"))
        price_amount_el = ET.SubElement(price, _cbc("PriceAmount"))
        price_amount_el.set("currencyID", currency)
        price_amount_el.text = str(_amount(line.unit_price))

    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="UTF-8")
