from __future__ import annotations

import datetime as dt
import re
import shlex
from decimal import Decimal, InvalidOperation

from django.db.models import Sum

BALANCE_SHEET_ACCOUNT_CLASSES = ["1", "2"]
RESULT_ACCOUNT_CLASSES = ["3", "4", "5", "6", "7", "8", "9", "10"]


NUMERIC_RE = re.compile(r"^[+-]?\d+(?:[\.,]\d+)?$")


# JournalEntry.debit/credit are max_digits=15, decimal_places=2.
MAX_SIE_AMOUNT = Decimal("10") ** 13


def _parse_date(raw: str) -> dt.date:
    raw = raw.strip()
    if raw.isdigit() and len(raw) in (6, 8):
        try:
            return dt.datetime.strptime(raw, "%Y%m%d" if len(raw) == 8 else "%y%m%d").date()
        except ValueError:
            pass
    raise ValueError(f"Ogiltigt datum i SIE: {raw}")


def _parse_decimal(raw: str) -> Decimal:
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Ogiltigt belopp i SIE: {raw}") from exc
    quantized = value.quantize(Decimal("0.01"))
    if quantized != value or abs(quantized) >= MAX_SIE_AMOUNT:
        raise ValueError(f"Ogiltigt belopp i SIE: {raw}")
    return quantized


def _sie_quote(value: str) -> str:
    safe = (value or "").replace('"', "'")
    return f'"{safe}"'


def _format_sie_amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def _tokenize_sie_line(line: str, line_number: int, diagnostics: list[str]) -> list[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        diagnostics.append(f"Rad {line_number}: kunde inte tolka raden ({exc}).")
        return []


def parse_sie_verifications_with_diagnostics(text: str) -> tuple[list[dict], list[str]]:
    """Parse #VER and #TRANS blocks and return (verifications, diagnostics)."""

    verifications: list[dict] = []
    diagnostics: list[str] = []
    current: dict | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{") or line.startswith("}"):
            continue
        if not line.startswith("#"):
            continue

        parts = _tokenize_sie_line(line, line_number, diagnostics)
        if not parts:
            continue

        tag = parts[0].upper()

        if tag == "#VER":
            if current:
                verifications.append(current)
            if len(parts) < 4:
                diagnostics.append(f"Rad {line_number}: #VER saknar obligatoriska fält (serie, nummer, datum).")
                current = None
                continue

            try:
                ver_date = _parse_date(parts[3])
            except ValueError as exc:
                diagnostics.append(f"Rad {line_number}: {exc}")
                current = None
                continue

            current = {
                "series": parts[1] or "A",
                "number": parts[2],
                "date": ver_date,
                "description": parts[4] if len(parts) > 4 else "",
                "entries": [],
            }
            continue

        if tag == "#TRANS":
            if current is None:
                diagnostics.append(f"Rad {line_number}: #TRANS hittades utan aktiv #VER.")
                continue
            if len(parts) < 3:
                diagnostics.append(f"Rad {line_number}: #TRANS saknar konto eller belopp.")
                continue

            account = parts[1]
            if not account.isdigit():
                diagnostics.append(f"Rad {line_number}: kontonummer '{account}' är ogiltigt.")
                continue

            amount: Decimal | None = None
            for token in parts[2:]:
                if not NUMERIC_RE.match(token):
                    continue
                try:
                    amount = _parse_decimal(token)
                except ValueError as exc:
                    diagnostics.append(f"Rad {line_number}: {exc}")
                break

            if amount is None:
                diagnostics.append(f"Rad {line_number}: kunde inte hitta ett giltigt belopp i #TRANS.")
                continue

            current["entries"].append({"account": account, "amount": amount})

    if current:
        verifications.append(current)

    return verifications, diagnostics


def parse_sie_verifications(text: str) -> list[dict]:
    """Parse #VER/#TRANS blocks and return imported verifications only."""

    verifications, _diagnostics = parse_sie_verifications_with_diagnostics(text)
    return verifications


def parse_sie_accounts(text: str) -> dict[str, str]:
    """Parse #KONTO lines into {account_number: name}, so accounts auto-created
    during import can use the source system's real account name instead of a
    generic placeholder."""

    accounts: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("#"):
            continue
        parts = _tokenize_sie_line(line, line_number, [])
        if len(parts) < 3 or parts[0].upper() != "#KONTO":
            continue
        accounts[parts[1]] = parts[2]
    return accounts


def parse_sie_balances(text: str, tag: str) -> dict[str, Decimal]:
    """Parse #IB/#UB lines for year 0 (this year) into {account_number: amount}.
    Comparison-year rows (-1, -2, ...) are ignored - this app only imports into
    a single accounting year at a time."""

    balances: dict[str, Decimal] = {}
    tag = tag.upper()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("#"):
            continue
        parts = _tokenize_sie_line(line, line_number, [])
        if len(parts) < 4 or parts[0].upper() != tag or parts[1] != "0":
            continue
        try:
            balances[parts[2]] = _parse_decimal(parts[3])
        except ValueError:
            continue
    return balances


def reconcile_closing_balances(company, accounting_year, expected_ub):
    """Compare each account's #UB from an imported SIE file against the balance
    actually recorded in the ledger as of the accounting year's end date.
    Returns a list of Swedish diagnostic messages for accounts that disagree
    (empty if everything reconciles, or if the file had no #UB lines)."""

    if not expected_ub:
        return []

    from .models import Account

    accounts_by_number = {
        account.number: account for account in Account.objects.filter(company=company, number__in=expected_ub.keys())
    }
    actual_balances = _account_net_amounts_by_id(
        [account.pk for account in accounts_by_number.values()],
        company=company,
        upto_date=accounting_year.end_date,
    )

    mismatches = []
    for number, expected_amount in sorted(expected_ub.items()):
        account = accounts_by_number.get(number)
        if account is None:
            mismatches.append(
                f"Konto {number}: saknas i kontoplanen, kunde inte stämma av mot filens UB {_format_sie_amount(expected_amount)}."
            )
            continue
        actual_amount = actual_balances.get(account.pk, Decimal("0"))
        if actual_amount != expected_amount:
            mismatches.append(
                f"Konto {number}: filens UB är {_format_sie_amount(expected_amount)} men bokfört saldo efter import är "
                f"{_format_sie_amount(actual_amount)}."
            )
    return mismatches


def _account_net_amounts_by_id(account_ids, *, company, upto_date=None, accounting_year=None):
    """One aggregate query per call instead of one per account (avoids N+1 on exports
    with a full BAS chart of accounts)."""
    from .models import JournalEntry

    if not account_ids:
        return {}
    entries = JournalEntry.objects.filter(account_id__in=account_ids, transaction__accounting_year__company=company)
    if accounting_year is not None:
        entries = entries.filter(transaction__accounting_year=accounting_year)
    if upto_date is not None:
        entries = entries.filter(transaction__date__lte=upto_date)
    grouped = entries.values("account_id").annotate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    return {
        row["account_id"]: (row["total_debit"] or Decimal("0")) - (row["total_credit"] or Decimal("0"))
        for row in grouped
    }


# Tecken utanför cp437 (SIE #FORMAT PC8) som annars tyst blir "?" i exporten.
_CP437_FALLBACKS = str.maketrans(
    {"–": "-", "—": "-", "‘": "'", "’": "'", "“": "'", "”": "'", "…": "...", "\u00a0": " ", "€": "EUR"}
)


def encode_sie(text: str) -> bytes:
    """cp437-bytes för nedladdning; translittererar vanliga tecken cp437 saknar."""
    return text.translate(_CP437_FALLBACKS).encode("cp437", errors="replace")


def generate_sie4_content(company, accounting_year, transactions) -> str:
    """Generate a deterministic SIE4 export for one accounting year."""

    org_number = (company.org_number or "").replace("-", "").replace(" ", "")
    lines = [
        "#FLAGGA 0",
        '#PROGRAM "SaldoVibe" "1.0"',
        "#FORMAT PC8",
        "#SIETYP 4",
        f"#GEN {dt.date.today().strftime('%Y%m%d')}",
        f"#FNAMN {_sie_quote(company.name)}",
    ]
    if org_number:
        lines.append(f"#ORGNR {org_number}")
    lines.append(
        f"#RAR 0 {accounting_year.start_date.strftime('%Y%m%d')} {accounting_year.end_date.strftime('%Y%m%d')}"
    )

    accounts = list(
        company.accounts.filter(is_active=True)
        .order_by("number", "id")
        .only("number", "name", "account_class", "sru_code")
    )
    for account in accounts:
        lines.append(f"#KONTO {account.number} {_sie_quote(account.name)}")

    for account in accounts:
        if account.sru_code:
            lines.append(f"#SRU {account.number} {account.sru_code}")

    balance_sheet_account_ids = [a.pk for a in accounts if a.account_class in BALANCE_SHEET_ACCOUNT_CLASSES]
    result_account_ids = [a.pk for a in accounts if a.account_class in RESULT_ACCOUNT_CLASSES]
    day_before_start = accounting_year.start_date - dt.timedelta(days=1)
    opening_balances = _account_net_amounts_by_id(
        balance_sheet_account_ids, company=company, upto_date=day_before_start
    )
    closing_balances = _account_net_amounts_by_id(
        balance_sheet_account_ids, company=company, upto_date=accounting_year.end_date
    )
    result_balances = _account_net_amounts_by_id(result_account_ids, company=company, accounting_year=accounting_year)

    for account in accounts:
        if account.account_class not in BALANCE_SHEET_ACCOUNT_CLASSES:
            continue
        opening = opening_balances.get(account.pk, Decimal("0"))
        if opening != Decimal("0"):
            lines.append(f"#IB 0 {account.number} {_format_sie_amount(opening)}")
        closing = closing_balances.get(account.pk, Decimal("0"))
        if closing != Decimal("0"):
            lines.append(f"#UB 0 {account.number} {_format_sie_amount(closing)}")

    for account in accounts:
        if account.account_class not in RESULT_ACCOUNT_CLASSES:
            continue
        result = result_balances.get(account.pk, Decimal("0"))
        if result != Decimal("0"):
            lines.append(f"#RES 0 {account.number} {_format_sie_amount(result)}")

    ordered_transactions = transactions.order_by("date", "voucher_series", "voucher_number", "id")
    for txn in ordered_transactions:
        series = (txn.voucher_series or "A").strip() or "A"
        number = str(txn.voucher_number or txn.pk)
        lines.append(
            f"#VER {_sie_quote(series)} {_sie_quote(number)} {txn.date.strftime('%Y%m%d')} {_sie_quote(txn.description)}"
        )
        lines.append("{")
        for entry in txn.entries.select_related("account").all().order_by("id"):
            amount = (entry.debit or Decimal("0")) - (entry.credit or Decimal("0"))
            lines.append(
                " ".join(
                    [
                        f"#TRANS {entry.account.number}",
                        "{}",
                        _format_sie_amount(amount),
                        txn.date.strftime("%Y%m%d"),
                        _sie_quote(entry.description or ""),
                    ]
                )
            )
        lines.append("}")

    return "\r\n".join(lines) + "\r\n"
