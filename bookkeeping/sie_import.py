"""Session/preview helpers for the SIE and SI import flows."""

import logging
from datetime import date
from decimal import Decimal

from django.utils import timezone

from .models import (
    Account,
    Transaction,
)

logger = logging.getLogger(__name__)
SIE_IMPORT_PREVIEW_SESSION_KEY = "sie_import_preview"
SI_REFERENCE_PREFIX = "SI-Import"
SIE_IMPORT_DIAGNOSTICS_SESSION_KEY = "sie_import_diagnostics"


def sie_preview_session_key(user_id, company_id, year_id):
    return f"{SIE_IMPORT_PREVIEW_SESSION_KEY}:{user_id}:{company_id}:{year_id}"


def sie_diagnostics_session_key(user_id, company_id, year_id):
    return f"{SIE_IMPORT_DIAGNOSTICS_SESSION_KEY}:{user_id}:{company_id}:{year_id}"


def serialize_sie_verifications(verifications):
    return [
        {
            "series": ver.get("series") or "",
            "number": ver.get("number") or "",
            "date": ver["date"].isoformat(),
            "description": ver.get("description") or "",
            "entries": [
                {
                    "account": entry["account"],
                    "amount": str(entry["amount"]),
                }
                for entry in ver.get("entries", [])
            ],
        }
        for ver in verifications
    ]


def deserialize_sie_verifications(serialized):
    return [
        {
            "series": "A",
            "number": ver.get("number") or "",
            "date": date.fromisoformat(ver["date"]),
            "description": ver.get("description") or "",
            "entries": [
                {
                    "account": entry["account"],
                    "amount": Decimal(entry["amount"]),
                }
                for entry in ver.get("entries", [])
            ],
        }
        for ver in serialized
    ]


def normalized_description(text):
    return " ".join((text or "").strip().lower().split())


def amount_signature(amount):
    return str((amount or Decimal("0")).quantize(Decimal("0.01")))


def verification_signature(ver):
    entry_signature = tuple(
        sorted((entry["account"], amount_signature(entry["amount"])) for entry in ver.get("entries", []))
    )
    return (
        ver["date"],
        normalized_description(ver.get("description") or ""),
        entry_signature,
    )


def transaction_signature(txn):
    entry_signature = tuple(
        sorted(
            (
                entry.account.number,
                amount_signature((entry.debit or Decimal("0")) - (entry.credit or Decimal("0"))),
            )
            for entry in txn.entries.all()
        )
    )
    return (
        txn.date,
        normalized_description(txn.description),
        entry_signature,
    )


def find_likely_duplicate_verifications(verifications, accounting_year):
    existing_by_signature = {}
    existing_transactions = Transaction.objects.filter(accounting_year=accounting_year).prefetch_related(
        "entries__account"
    )
    for txn in existing_transactions:
        sig = transaction_signature(txn)
        existing_by_signature.setdefault(sig, []).append(txn.reference or f"ID {txn.pk}")

    duplicate_map = {}
    for idx, ver in enumerate(verifications):
        sig = verification_signature(ver)
        matching_refs = existing_by_signature.get(sig, [])
        if matching_refs:
            duplicate_map[idx] = matching_refs

    return duplicate_map


def build_si_verification_preview(verifications, company, posted_mapping=None, duplicate_map=None):
    existing_accounts = list(Account.objects.filter(company=company, is_active=True).order_by("number", "name"))
    existing_by_number = {acc.number: acc for acc in existing_accounts}

    groups = []
    for ver_idx, ver in enumerate(verifications):
        reference = f"{ver.get('series', '')}{ver.get('number', '')}"
        group_rows = []
        total_amount = Decimal("0")
        for entry_idx, entry in enumerate(ver.get("entries", [])):
            source_number = entry["account"]
            target_number = source_number
            if posted_mapping is not None:
                target_number = posted_mapping.get((ver_idx, entry_idx), source_number)

            existing_source = existing_by_number.get(source_number)
            existing_target = existing_by_number.get(target_number)
            amount = entry["amount"]
            total_amount += abs(amount)
            group_rows.append(
                {
                    "field_name": f"account_map_{ver_idx}_{entry_idx}",
                    "source_number": source_number,
                    "source_name": existing_source.name if existing_source is not None else "",
                    "target_number": target_number,
                    "amount": amount,
                    "is_debit": amount >= 0,
                    "target_exists": existing_target is not None,
                    "target_name": existing_target.name if existing_target is not None else "",
                }
            )

        groups.append(
            {
                "ver_idx": ver_idx,
                "reference": reference,
                "date": ver["date"],
                "description": ver.get("description") or "",
                "rows": group_rows,
                "entry_count": len(group_rows),
                "total_amount": total_amount,
                "likely_duplicate": bool((duplicate_map or {}).get(ver_idx)),
                "duplicate_refs": (duplicate_map or {}).get(ver_idx, []),
            }
        )

    return {
        "groups": groups,
        "existing_accounts": existing_accounts,
    }


def next_si_series_number(accounting_year):
    max_number = 0
    for ref in Transaction.objects.filter(accounting_year=accounting_year).values_list("reference", flat=True):
        raw_ref = (ref or "").strip().upper()
        expected_prefix = SI_REFERENCE_PREFIX.upper()
        if not raw_ref.startswith(expected_prefix):
            continue
        number_part = raw_ref[len(expected_prefix) :].strip()
        if number_part.startswith("-"):
            number_part = number_part[1:].strip()
        if not number_part.isdigit():
            continue
        max_number = max(max_number, int(number_part))
    return max_number + 1


def summarize_import_diagnostics(diagnostics):
    if not diagnostics:
        return None
    preview_lines = diagnostics[:5]
    suffix = "" if len(diagnostics) <= 5 else f" (+{len(diagnostics) - 5} ytterligare)"
    return " | ".join(preview_lines) + suffix


def serialize_import_diagnostics_payload(source, diagnostics):
    return {
        "source": source,
        "count": len(diagnostics),
        "diagnostics": diagnostics,
        "generated_at": timezone.now().isoformat(),
    }
