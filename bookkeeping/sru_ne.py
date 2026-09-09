"""BAS-konto -> fältkod på NE-bilagan (Inkomst av näringsverksamhet, enskilda
näringsidkare som inte upprättar förenklat årsbokslut).

Källa: BAS-intressenternas förening, "NE - Inkomst av näringsverksamhet, Enskilda
näringsidkare" (NE_EJ_K1-Intervall-231002.pdf). Kopplingen härleds från kontonumret
vid exporten - Account.sru_code (INK2) rörs inte, så samma kontoplan kan användas
oavsett bolagsform.

Teckenkonvention (blankettens belopp är positiva): tillgångar och kostnader är
debet - kredit, eget kapital/skulder och intäkter är kredit - debet. R11 (bokfört
resultat) räknas fram som R1-R4 minus R5-R10; 899x tas därför inte med.
"""

from decimal import Decimal

NE_FIELDS = {
    "7200": "B1 Immateriella anläggningstillgångar",
    "7210": "B2 Byggnader och markanläggningar",
    "7211": "B3 Mark och andra tillgångar som inte får skrivas av",
    "7212": "B4 Maskiner och inventarier",
    "7213": "B5 Övriga anläggningstillgångar",
    "7240": "B6 Varulager",
    "7250": "B7 Kundfordringar",
    "7260": "B8 Övriga fordringar",
    "7280": "B9 Kassa och bank",
    "7300": "B10 Eget kapital",
    "7320": "B11 Obeskattade reserver",
    "7330": "B12 Avsättningar",
    "7380": "B13 Låneskulder",
    "7382": "B15 Leverantörsskulder",
    "7383": "B16 Övriga skulder",
    "7400": "R1 Försäljning och utfört arbete samt övriga momspliktiga intäkter",
    "7401": "R2 Momsfria intäkter",
    "7403": "R4 Ränteintäkter m.m.",
    "7500": "R5 Varor, material och tjänster",
    "7501": "R6 Övriga externa kostnader",
    "7502": "R7 Anställd personal",
    "7503": "R8 Räntekostnader m.m.",
    "7504": "R9 Avskrivningar och nedskrivningar byggnader och markanläggningar",
    "7505": "R10 Avskrivningar och nedskrivningar maskiner och inventarier och immateriella tillgångar",
    "7440": "R11 Bokfört resultat",
}

# Fältkoder där beloppet är kredit - debet (skulder, eget kapital, intäkter).
CREDIT_FIELDS = {"7300", "7320", "7330", "7380", "7382", "7383", "7400", "7401", "7403"}
INCOME_FIELDS = {"7400", "7401", "7403"}
COST_FIELDS = {"7500", "7501", "7502", "7503", "7504", "7505"}
RESULT_FIELD = "7440"

# Momsfält (Account.vat_field_code) som betyder momspliktig försäljning -> R1, övrigt -> R2.
VAT_LIABLE_SALES_FIELDS = {"05", "06", "07", "08"}

_FIXED_RANGES = [
    (1000, 1099, "7200"),
    (1100, 1129, "7210"),
    (1130, 1149, "7211"),
    (1150, 1179, "7210"),
    (1180, 1189, "7211"),
    (1190, 1199, "7210"),
    (1200, 1290, "7212"),
    (1291, 1291, "7211"),
    (1292, 1299, "7212"),
    (1300, 1399, "7213"),
    (1400, 1499, "7240"),
    (1500, 1599, "7250"),
    (1600, 1899, "7260"),
    (1900, 1999, "7280"),
    (2010, 2019, "7300"),
    (2050, 2059, "7300"),
    (2100, 2199, "7320"),
    (2200, 2299, "7330"),
    (2300, 2399, "7380"),
    (2410, 2419, "7380"),
    (2420, 2439, "7383"),
    (2440, 2449, "7382"),
    (2450, 2459, "7383"),
    (2460, 2479, "7382"),
    (2480, 2489, "7380"),
    (2490, 2499, "7383"),
    (2600, 2999, "7383"),
    (3800, 3899, "7403"),
    (4000, 4999, "7500"),
    (5000, 6999, "7501"),
    (7000, 7699, "7502"),
    (7710, 7719, "7505"),
    (7720, 7729, "7504"),
    (7730, 7739, "7505"),
    (7740, 7749, "7503"),
    (7760, 7769, "7505"),
    (7770, 7779, "7504"),
    (7780, 7789, "7505"),
    (7790, 7799, "7503"),
    (7810, 7819, "7505"),
    (7820, 7829, "7504"),
    (7830, 7839, "7505"),
    (7840, 7849, "7504"),
    (7900, 7999, "7503"),
    (8010, 8019, "7403"),
    (8070, 8089, "7503"),
    (8110, 8119, "7403"),
    (8170, 8189, "7503"),
    (8200, 8219, "7403"),
    (8250, 8269, "7403"),
    (8270, 8289, "7503"),
    (8300, 8319, "7403"),
    (8340, 8349, "7403"),
    (8360, 8369, "7403"),
    (8370, 8389, "7503"),
    (8390, 8399, "7403"),
    (8400, 8429, "7503"),
    (8440, 8449, "7403"),
    (8460, 8469, "7503"),
    (8480, 8489, "7503"),
    (8850, 8859, "7505"),
    (8900, 8989, "7503"),
]

# Konton som hamnar på R4 om saldot är en intäkt och på R8 om det är en kostnad.
_SIGN_DEPENDENT_PREFIXES = {
    802,
    803,
    812,
    813,
    822,
    823,
    824,
    829,
    832,
    833,
    835,
    843,
    845,
    849,
    881,
    886,
    888,
    889,
}

# Konton som enligt kopplingstabellen medvetet inte redovisas på NE-bilagan:
# skatteskulder (B14 "Inget redovisas här") och årets resultat (R11 räknas fram).
_INTENTIONALLY_UNMAPPED_RANGES = [(2500, 2599), (8990, 8999)]


def is_intentionally_unmapped(number):
    try:
        n = int(str(number)[:4])
    except (ValueError, TypeError):
        return False
    return any(lo <= n <= hi for lo, hi in _INTENTIONALLY_UNMAPPED_RANGES)


def resolve_ne_code(number, *, vat_field_code="", credit_net=Decimal("0")):
    """Fältkod på NE-bilagan för ett BAS-konto, eller '' om kontot inte kopplas.

    ``credit_net`` (kredit - debet) avgör R4/R8 för de finansiella konton som
    kopplingstabellen listar med (+)/(-)."""
    try:
        n = int(str(number)[:4])
    except (ValueError, TypeError):
        return ""

    if 3000 <= n <= 3799 or 3900 <= n <= 3999:
        return "7400" if vat_field_code in VAT_LIABLE_SALES_FIELDS else "7401"
    if n // 10 in _SIGN_DEPENDENT_PREFIXES:
        return "7403" if credit_net >= 0 else "7503"
    for lo, hi, code in _FIXED_RANGES:
        if lo <= n <= hi:
            return code
    return ""


def ne_field_amount(code, debit, credit):
    """Beloppet med blankettens tecken för fältkoden."""
    return credit - debit if code in CREDIT_FIELDS else debit - credit


def ne_result(totals):
    """R11: intäkter minus kostnader, från summerade fältbelopp."""
    income = sum((amount for code, amount in totals.items() if code in INCOME_FIELDS), Decimal("0"))
    costs = sum((amount for code, amount in totals.items() if code in COST_FIELDS), Decimal("0"))
    return income - costs
