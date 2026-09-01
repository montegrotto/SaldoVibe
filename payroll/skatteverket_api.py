import json
import logging
import ssl
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from django.conf import settings

logger = logging.getLogger(__name__)


def _build_ssl_context():
    ca_bundle = (getattr(settings, "SKATTEVERKET_CA_BUNDLE", "") or "").strip()
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)

    use_certifi = bool(getattr(settings, "SKATTEVERKET_USE_CERTIFI", True))
    if use_certifi:
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            # Fallback to default system trust store when certifi is unavailable.
            pass

    return ssl.create_default_context()


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _extract_tax_amount(payload):
    if not isinstance(payload, dict):
        return None

    candidate_keys = [
        "tax_amount",
        "preliminary_tax_amount",
        "preliminar_skatt_belopp",
        "preliminarSkattBelopp",
        "preliminaryTaxAmount",
    ]
    for key in candidate_keys:
        amount = _to_decimal(payload.get(key))
        if amount is not None:
            return amount
    return None


def _as_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ["results", "rows", "items", "data"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _rowstore_endpoint(url):
    normalized = (url or "").strip().rstrip("/")
    if normalized.endswith("/html"):
        normalized = normalized[:-5]
    if normalized.endswith("/json"):
        return normalized
    return f"{normalized}/json"


def _lookup_tax_amount_from_rowstore(*, url, table_number, table_column, taxable_salary, payment_date, timeout):
    endpoint = _rowstore_endpoint(url)
    income = int(Decimal(str(taxable_salary or "0")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    year = str(getattr(payment_date, "year", "") or "")
    params = {
        "tabellnr": str(table_number),
        "_limit": "500",
    }
    if year:
        params["år"] = year

    query_url = f"{endpoint}?{urllib_parse.urlencode(params)}"
    request = urllib_request.Request(query_url, headers={"Accept": "application/json"}, method="GET")

    with urllib_request.urlopen(request, timeout=timeout, context=_build_ssl_context()) as response:
        response_body = response.read().decode("utf-8", errors="ignore")
        response_payload = json.loads(response_body) if response_body else {}

    rows = _as_rows(response_payload)
    if not rows:
        raise ValueError("Skattetabell API returnerade inga rader.")

    col_key = f"kolumn {int(table_column)}"
    for row in rows:
        if not isinstance(row, dict):
            continue
        low = _to_decimal(row.get("inkomst fr.o.m."))
        high = _to_decimal(row.get("inkomst t.o.m."))
        if low is None or high is None:
            continue
        if int(low) <= income <= int(high):
            tax_amount = _to_decimal(row.get(col_key))
            if tax_amount is None:
                raise ValueError(f"Skattetabellrad saknar värde för {col_key}.")
            reference = f"rowstore:{row.get('tabellnr', table_number)}:{row.get('år', year)}:{int(low)}-{int(high)}"
            return {"tax_amount": tax_amount, "reference": reference}

    raise ValueError(
        f"Kunde inte hitta skattetabellrad för tabell {table_number}, kolumn {table_column}, inkomst {income}."
    )


def get_tax_amount_from_skatteverket(
    *, taxable_salary, table_number, table_column, payment_date, personal_identity_number, company_org_number
):
    base_url = (getattr(settings, "SKATTEVERKET_API_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("Skatteverket API är inte konfigurerat (saknar SKATTEVERKET_API_BASE_URL).")

    tax_path = (getattr(settings, "SKATTEVERKET_API_TAX_PATH", "") or "").strip()
    if not tax_path.startswith("/"):
        tax_path = f"/{tax_path}"

    url = f"{base_url}{tax_path}"
    timeout = int(getattr(settings, "SKATTEVERKET_API_TIMEOUT", 12) or 12)

    # RowStore dataset endpoint published by Skatteverket.
    if "/rowstore/dataset/" in url:
        try:
            return _lookup_tax_amount_from_rowstore(
                url=url,
                table_number=table_number,
                table_column=table_column,
                taxable_salary=taxable_salary,
                payment_date=payment_date,
                timeout=timeout,
            )
        except (urllib_error.HTTPError, urllib_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skatteverket RowStore lookup failed: %s", exc)
            raise ValueError(f"Skatteverket API-anrop misslyckades: {exc}")

    payload = {
        "income": str(taxable_salary),
        "taxable_salary": str(taxable_salary),
        "tax_table": int(table_number),
        "tax_table_number": int(table_number),
        "column": int(table_column),
        "tax_table_column": int(table_column),
        "payment_date": str(payment_date),
        "personal_identity_number": (personal_identity_number or "").strip(),
        "company_org_number": (company_org_number or "").strip(),
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    api_key = (getattr(settings, "SKATTEVERKET_API_KEY", "") or "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    bearer_token = (getattr(settings, "SKATTEVERKET_API_BEARER_TOKEN", "") or "").strip()
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    request = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=timeout, context=_build_ssl_context()) as response:
            response_body = response.read().decode("utf-8", errors="ignore")
            response_payload = json.loads(response_body) if response_body else {}
            tax_amount = _extract_tax_amount(response_payload)
            if tax_amount is None:
                raise ValueError("Skatteverket API-svar saknar fält för skattebelopp.")
            reference = (
                response_payload.get("request_id")
                or response_payload.get("reference")
                or response_payload.get("id")
                or ""
            )
            return {
                "tax_amount": tax_amount,
                "reference": str(reference),
            }
    except (urllib_error.HTTPError, urllib_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Skatteverket API lookup failed: %s", exc)
        raise ValueError(f"Skatteverket API-anrop misslyckades: {exc}")
