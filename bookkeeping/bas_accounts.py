import json
from functools import lru_cache
from pathlib import Path

from .models import Account


class BasAccountLoadError(Exception):
    pass


_DATA_FILE = Path(__file__).resolve().parent / "data" / "bas_2026_accounts.json"


def _normalize_account_row(row):
    number = str(row.get("number", "")).strip()
    name = str(row.get("name", "")).strip()
    account_class = str(row.get("account_class", "")).strip()
    sru_code = str(row.get("sru_code", "")).strip()
    vat_field_code = str(row.get("vat_field_code", "")).strip()
    allowed_vat_codes = {choice[0] for choice in Account.VatFieldCode.choices}

    if not number.isdigit() or len(number) != 4:
        raise BasAccountLoadError(f"Ogiltigt kontonummer i BAS-data: {number!r}")
    if not name:
        raise BasAccountLoadError(f"Tomt kontonamn i BAS-data för konto {number}")
    if not account_class:
        account_class = number[0]
    if vat_field_code and vat_field_code not in allowed_vat_codes:
        raise BasAccountLoadError(f"Ogiltig momskod i BAS-data för konto {number}: {vat_field_code!r}")

    return {
        "number": number,
        "name": name,
        "account_class": account_class,
        "sru_code": sru_code,
        "vat_field_code": vat_field_code,
    }


@lru_cache(maxsize=1)
def load_bas_2026_accounts():
    if not _DATA_FILE.exists():
        raise BasAccountLoadError(f"BAS-datafil saknas: {_DATA_FILE}")

    try:
        payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BasAccountLoadError(f"Kunde inte tolka BAS-datafilen: {_DATA_FILE}") from exc

    if not isinstance(payload, list) or not payload:
        raise BasAccountLoadError("BAS-datafilen innehaller inga konton")

    return tuple(_normalize_account_row(row) for row in payload)


@lru_cache(maxsize=1)
def _bas_2026_accounts_by_number():
    return {row["number"]: row for row in load_bas_2026_accounts()}


def lookup_bas_account(number):
    """Return the BAS 2026 reference row for an exact 4-digit account number, or None."""
    return _bas_2026_accounts_by_number().get(str(number).strip())


def seed_bas_2026_accounts_for_company(company):
    accounts = load_bas_2026_accounts()

    existing_numbers = set(Account.objects.filter(company=company).values_list("number", flat=True))

    to_create = [
        Account(
            company=company,
            number=row["number"],
            name=row["name"],
            account_class=row["account_class"],
            sru_code=row.get("sru_code", ""),
            vat_field_code=row.get("vat_field_code", ""),
        )
        for row in accounts
        if row["number"] not in existing_numbers
    ]

    if not to_create:
        return 0

    # ponytail: bulk_create hoppar över auditlogg-signalerna med flit — ~1300 hashkedjade
    # poster per nytt företag är brus, och BAS-kontoplanen är referensdata, inte affärshändelser.
    # Baksida: reconcile_audit_log kan inte upptäcka ett seedat konto som raderats bakvägen.
    # Byt till save()-loop om kontoraderna någon gång behöver full behandlingshistorik.
    Account.objects.bulk_create(to_create, batch_size=500)
    return len(to_create)
