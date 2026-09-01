import calendar
import hashlib
import json
import re
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from django.db.models import Q, Sum

from bookkeeping.models import JournalEntry

ZERO = Decimal("0.00")
VAT_CLOSING_REFERENCE_PREFIX = "VATCLOSE:"

SKATTEVERKET_FIELD_CODES = [
    "05",
    "06",
    "07",
    "08",
    "10",
    "11",
    "12",
    "20",
    "21",
    "22",
    "23",
    "24",
    "30",
    "31",
    "32",
    "35",
    "36",
    "37",
    "38",
    "39",
    "48",
    "49",
    "50",
]

# Purchase/expense-side boxes grow with debit rather than the default credit-minus-debit
# convention used for sales and VAT-liability boxes. Box 37 is a purchase amount too
# (Mellanmans INKÖP av varor vid trepartshandel) even though it sits in the form's "E"
# (sales) section, so it follows the same debit-minus-credit convention.
PURCHASE_SIDE_FIELD_CODES = {"20", "21", "22", "23", "24", "37", "48"}

# Grouping of Skatteverket's momsdeklaration boxes into the sections used on the official
# form (SKV 4700) — codes 40-42 (övrig undantagen försäljning) aren't tracked by this
# application, so group E stops at 39.
SKATTEVERKET_FIELD_GROUPS = [
    ("A", "Momspliktig försäljning eller uttag exklusive moms", ["05", "06", "07", "08"]),
    ("B", "Utgående moms på försäljning eller uttag i ruta 05-08", ["10", "11", "12"]),
    ("C", "Momspliktiga inköp där köparen är skattskyldig", ["20", "21", "22", "23", "24"]),
    ("D", "Utgående moms på inköp i ruta 20-24", ["30", "31", "32"]),
    ("E", "Försäljning m.m. som är undantagen från moms", ["35", "36", "37", "38", "39"]),
    ("F", "Ingående moms att dra av", ["48"]),
    ("G", "Moms att betala eller få tillbaka", ["49", "50"]),
]

SKATTEVERKET_FIELD_LABELS = {
    "05": "Momspliktig försäljning som inte ingår i annan ruta",
    "06": "Momspliktiga uttag",
    "07": "Beskattningsunderlag vid vinstmarginalbeskattning",
    "08": "Hyresinkomster vid frivillig skattskyldighet",
    "10": "Utgående moms 25 %",
    "11": "Utgående moms 12 %",
    "12": "Utgående moms 6 %",
    "20": "Inköp av varor från annat EU-land",
    "21": "Inköp av tjänster från annat EU-land enligt huvudregeln",
    "22": "Inköp av tjänster från länder utanför EU",
    "23": "Inköp av varor i Sverige (omvänd skattskyldighet)",
    "24": "Övriga inköp av tjänster (omvänd skattskyldighet)",
    "30": "Utgående moms på inköp i ruta 20-24",
    "31": "Utgående moms på inköp i ruta 20-24",
    "32": "Utgående moms på inköp i ruta 20-24",
    "35": "Försäljning av varor till annat EU-land",
    "36": "Försäljning av varor utanför EU",
    "37": "Mellanmans inköp av varor vid trepartshandel",
    "38": "Tjänsteförsäljning till annat EU-land",
    "39": "Övrig försäljning av tjänster omsatta utanför Sverige",
    "48": "Ingående moms att dra av",
    "49": "Moms att betala",
    "50": "Moms att få tillbaka",
}

# Common BAS accounts used for Swedish VAT reporting.
STANDARD_VAT_ACCOUNTS = [
    ("2611", "Utgående moms, 25 %"),
    ("2621", "Utgående moms, 12 %"),
    ("2631", "Utgående moms, 6 %"),
    ("2641", "Debiterad ingående moms"),
    ("2650", "Redovisningskonto för moms"),
]

VAT_TO_INCOME_ACCOUNTS = {
    "2611": ["3001"],
    "2621": ["3002"],
    "2631": ["3003"],
    "2641": [],
    "2650": [],
}

INFERRED_VAT_FIELD_PREFIXES = [
    ("264", "48"),
    ("263", "12"),
    ("262", "11"),
    ("261", "10"),
    ("30", "05"),
]

VAT_CLOSING_FIELD_CODES = {"10", "11", "12", "30", "31", "32", "48"}


def build_vat_periods(accounting_year, reporting_period, today, vat_start_date=None):
    """Return the periods eligible for the VAT overview: already-closed periods and
    the single period currently in progress (marked with status "ongoing"). Periods
    that end before ``vat_start_date`` (the company's VAT reporting start date) and
    periods that have not started yet are excluded."""
    if accounting_year is None:
        return []

    if reporting_period == "annual":
        bounds = [(accounting_year.start_date, accounting_year.end_date)]
    elif reporting_period == "monthly":
        bounds = _monthly_period_bounds(accounting_year)
    elif reporting_period == "quarterly":
        bounds = _quarterly_period_bounds(accounting_year)
    else:
        return []

    periods = []
    for period_start, period_end in bounds:
        if period_start > today:
            continue
        if vat_start_date and period_end < vat_start_date:
            continue
        periods.append(
            {
                "key": _period_key(period_start, period_end),
                "label": _period_label(period_start, period_end),
                "start_date": period_start,
                "end_date": period_end,
                "status": "closed" if period_end < today else "ongoing",
            }
        )
    return periods


def build_closed_periods(accounting_year, reporting_period, today, vat_start_date=None):
    return [
        period
        for period in build_vat_periods(accounting_year, reporting_period, today, vat_start_date)
        if period["status"] == "closed"
    ]


def parse_period_key(value):
    if not value or ":" not in value:
        return None, None
    start_raw, end_raw = value.split(":", 1)
    try:
        return date.fromisoformat(start_raw), date.fromisoformat(end_raw)
    except ValueError:
        return None, None


def calculate_vat_boxes(company, start_date, end_date):
    boxes = {code: ZERO for code in SKATTEVERKET_FIELD_CODES}

    rows = (
        _get_vat_entry_queryset(company=company, start_date=start_date, end_date=end_date)
        .values("account__number", "account__vat_field_code")
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    )

    for row in rows:
        field_code = get_effective_vat_field_code(
            account_number=row.get("account__number") or "",
            configured_field_code=row.get("account__vat_field_code") or "",
        )
        total_debit = row.get("total_debit") or ZERO
        total_credit = row.get("total_credit") or ZERO

        if field_code in PURCHASE_SIDE_FIELD_CODES:
            boxes[field_code] += total_debit - total_credit
        elif field_code in boxes:
            boxes[field_code] += total_credit - total_debit

    box48 = boxes["48"]
    output_vat_total = boxes["10"] + boxes["11"] + boxes["12"] + boxes["30"] + boxes["31"] + boxes["32"]
    net_vat = output_vat_total - box48

    boxes["49"] = net_vat if net_vat > ZERO else ZERO
    boxes["50"] = -net_vat if net_vat < ZERO else ZERO

    def money(value):
        return value.quantize(Decimal("0.01"))

    return {code: money(value) for code, value in boxes.items()}


def get_standard_vat_account_rows(company, start_date=None, end_date=None):
    account_map = {account.number: account for account in company.accounts.all()}

    amounts = {}
    if start_date and end_date:
        rows = (
            JournalEntry.objects.filter(
                account__company=company,
                account__number__in=[number for number, _ in STANDARD_VAT_ACCOUNTS],
                transaction__date__gte=start_date,
                transaction__date__lte=end_date,
            )
            .values("account__number")
            .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
        )
        for row in rows:
            number = row.get("account__number")
            debit = row.get("total_debit") or ZERO
            credit = row.get("total_credit") or ZERO
            amounts[number] = (credit - debit).quantize(Decimal("0.01"))

    result = []
    for number, label in STANDARD_VAT_ACCOUNTS:
        account = account_map.get(number)
        related_income_accounts = []
        for income_number in VAT_TO_INCOME_ACCOUNTS.get(number, []):
            income_account = account_map.get(income_number)
            related_income_accounts.append(
                {
                    "number": income_number,
                    "is_configured": income_account is not None,
                    "name": income_account.name if income_account else "Ej upplagt i kontoplanen",
                }
            )
        result.append(
            {
                "number": number,
                "label": label,
                "is_configured": account is not None,
                "account_name": account.name if account else "Ej upplagt i kontoplanen",
                "balance": amounts.get(number, ZERO),
                "related_income_accounts": related_income_accounts,
            }
        )
    return result


def round_to_whole_krona(value):
    """Skatteverket's momsdeklaration reports every box in whole kronor, always
    rounded down (never to nearest) per Skatteverket's own rounding rule."""
    decimal_value = Decimal(str(value if value is not None else "0"))
    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_FLOOR))


def get_skatteverket_field_groups(vat_boxes):
    return [
        {
            "letter": letter,
            "label": label,
            "rows": [
                {
                    "code": code,
                    "label": SKATTEVERKET_FIELD_LABELS.get(code, ""),
                    "amount": round_to_whole_krona(vat_boxes.get(code, ZERO)),
                }
                for code in codes
            ],
        }
        for letter, label, codes in SKATTEVERKET_FIELD_GROUPS
    ]


def get_field_account_prefixes(field_code):
    if field_code in {"49", "50"}:
        return ["10", "11", "12", "30", "31", "32", "48"]
    if field_code in SKATTEVERKET_FIELD_CODES:
        return [field_code]
    return []


def infer_vat_field_code(account_number):
    normalized = (account_number or "").strip()
    for prefix, field_code in INFERRED_VAT_FIELD_PREFIXES:
        if normalized.startswith(prefix):
            return field_code
    return ""


def get_effective_vat_field_code(account_number, configured_field_code):
    return configured_field_code or infer_vat_field_code(account_number)


def get_field_account_query(field_code):
    field_codes = get_field_account_prefixes(field_code)
    if not field_codes:
        return Q(pk__isnull=True)

    query = Q(entries__account__vat_field_code__in=field_codes)
    for prefix, inferred_code in INFERRED_VAT_FIELD_PREFIXES:
        if inferred_code in field_codes:
            query |= Q(entries__account__vat_field_code="", entries__account__number__startswith=prefix)
    return query


def build_vat_closing_reference(start_date, end_date):
    return f"{VAT_CLOSING_REFERENCE_PREFIX}{start_date.isoformat()}:{end_date.isoformat()}"


def get_vat_closing_balances(company, start_date, end_date):
    rows = (
        _get_vat_entry_queryset(company=company, start_date=start_date, end_date=end_date)
        .values("account_id", "account__number", "account__name", "account__vat_field_code")
        .annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    )

    balances = []
    for row in rows:
        field_code = get_effective_vat_field_code(
            account_number=row.get("account__number") or "",
            configured_field_code=row.get("account__vat_field_code") or "",
        )
        if field_code not in VAT_CLOSING_FIELD_CODES:
            continue

        total_debit = row.get("total_debit") or ZERO
        total_credit = row.get("total_credit") or ZERO
        balance = (total_debit - total_credit) if field_code == "48" else (total_credit - total_debit)
        if balance == ZERO:
            continue

        balances.append(
            {
                "account_id": row["account_id"],
                "account_number": row.get("account__number") or "",
                "account_name": row.get("account__name") or "",
                "field_code": field_code,
                "balance": balance.quantize(Decimal("0.01")),
            }
        )

    return balances


def build_vat_source_fingerprint(company, start_date, end_date):
    rows = (
        _get_vat_entry_queryset(company=company, start_date=start_date, end_date=end_date)
        .values("transaction_id", "account_id", "debit", "credit")
        .order_by("transaction_id", "account_id", "id")
    )

    payload_rows = []
    transaction_ids = []
    seen_txn_ids = set()
    for row in rows:
        txn_id = row.get("transaction_id")
        if txn_id and txn_id not in seen_txn_ids:
            seen_txn_ids.add(txn_id)
            transaction_ids.append(txn_id)

        payload_rows.append(
            {
                "transaction_id": txn_id,
                "account_id": row.get("account_id"),
                "debit": format(row.get("debit") or ZERO, "f"),
                "credit": format(row.get("credit") or ZERO, "f"),
            }
        )

    serialized = json.dumps(payload_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return fingerprint, transaction_ids


def validate_eskd_export(company, start_date, end_date, vat_boxes):
    errors = []
    warnings = []

    org_number = (company.org_number or "").strip()
    if not org_number:
        errors.append("Organisationsnummer saknas. Fyll i organisationsnummer i foretagsinstallningar.")
    elif not re.match(r"^\d{6}-?\d{4}$", org_number):
        errors.append("Organisationsnummer har ogiltigt format. Anvand format YYMMDD-XXXX eller XXXXXX-XXXX.")

    missing_codes = [code for code in SKATTEVERKET_FIELD_CODES if code not in vat_boxes]
    if missing_codes:
        errors.append("Momsfalt saknas i berakningen: " + ", ".join(missing_codes))

    if vat_boxes.get("49", ZERO) > ZERO and vat_boxes.get("50", ZERO) > ZERO:
        errors.append("Bade MomsBetala (49) och Moms att fa tillbaka (50) ar satta samtidigt.")

    return {"errors": errors, "warnings": warnings}


def _get_vat_entry_queryset(*, company, start_date, end_date):
    return JournalEntry.objects.filter(
        account__company=company,
        transaction__date__gte=start_date,
        transaction__date__lte=end_date,
    ).exclude(transaction__reference__startswith=VAT_CLOSING_REFERENCE_PREFIX)


def _monthly_period_bounds(accounting_year):
    result = []
    current = date(accounting_year.start_date.year, accounting_year.start_date.month, 1)

    while current <= accounting_year.end_date:
        month_end = date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        period_start = max(current, accounting_year.start_date)
        period_end = min(month_end, accounting_year.end_date)

        if period_start <= period_end:
            result.append((period_start, period_end))

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return result


def _quarterly_period_bounds(accounting_year):
    result = []
    start_month = ((accounting_year.start_date.month - 1) // 3) * 3 + 1
    current = date(accounting_year.start_date.year, start_month, 1)

    while current <= accounting_year.end_date:
        quarter_end_month = current.month + 2
        quarter_end_year = current.year
        if quarter_end_month > 12:
            quarter_end_month -= 12
            quarter_end_year += 1
        quarter_end = date(
            quarter_end_year,
            quarter_end_month,
            calendar.monthrange(quarter_end_year, quarter_end_month)[1],
        )

        period_start = max(current, accounting_year.start_date)
        period_end = min(quarter_end, accounting_year.end_date)

        if period_start <= period_end:
            result.append((period_start, period_end))

        next_month = current.month + 3
        next_year = current.year
        if next_month > 12:
            next_month -= 12
            next_year += 1
        current = date(next_year, next_month, 1)

    return result


def _period_key(start_date, end_date):
    return f"{start_date.isoformat()}:{end_date.isoformat()}"


def _period_label(start_date, end_date):
    return f"{start_date.isoformat()} - {end_date.isoformat()}"
