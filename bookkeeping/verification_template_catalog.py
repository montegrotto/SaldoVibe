import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from django.db import transaction as db_transaction

from .bas_accounts import lookup_bas_account
from .models import Account, VerificationTemplate, VerificationTemplateEntry


class VerificationTemplateCatalogError(Exception):
    pass


_DATA_FILE = Path(__file__).resolve().parent / "data" / "verification_templates.json"

_CENT = Decimal("0.01")
_SIDES = {"debit", "credit"}
_AMOUNT_RULES = {choice[0] for choice in VerificationTemplateEntry.AmountRule.choices}


def _quantize(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def compute_template_amounts(entries, base_amount):
    """Beräkna radbelopp för en mall utifrån ett basbelopp.

    ``entries`` är en sekvens av dictar med nycklarna ``side`` ("debit"/"credit"),
    ``amount_rule`` och ``amount_percent``. Returnerar en lista med samma längd där
    varje post är ett ``Decimal`` eller ``None`` för rader som fylls i manuellt.

    Regeln ``remainder`` läggs sist och absorberar öresavrundningen, så att summa
    debet alltid är lika med summa kredit när alla rader har en regel.
    """
    base = _quantize(Decimal(base_amount))
    amounts = [None] * len(entries)
    totals = {"debit": Decimal("0.00"), "credit": Decimal("0.00")}
    remainder_index = None

    for index, entry in enumerate(entries):
        rule = entry["amount_rule"]
        if rule == VerificationTemplateEntry.AmountRule.PERCENT:
            amount = _quantize(base * Decimal(entry["amount_percent"]) / Decimal(100))
            amounts[index] = amount
            totals[entry["side"]] += amount
        elif rule == VerificationTemplateEntry.AmountRule.REMAINDER:
            remainder_index = index

    if remainder_index is not None:
        side = entries[remainder_index]["side"]
        opposite = "credit" if side == "debit" else "debit"
        amounts[remainder_index] = totals[opposite] - totals[side]

    return amounts


def entry_rules_from_model(entries):
    """Normalisera ``VerificationTemplateEntry``-objekt till indata för ``compute_template_amounts``."""
    return [
        {
            "side": "debit" if entry.is_debit else "credit",
            "amount_rule": entry.amount_rule,
            "amount_percent": entry.amount_percent,
        }
        for entry in entries
    ]


def _normalize_entry(entry, slug, index):
    where = f"mall {slug!r} rad {index + 1}"

    number = str(entry.get("account", "")).strip()
    if not number.isdigit() or len(number) != 4:
        raise VerificationTemplateCatalogError(f"Ogiltigt kontonummer i {where}: {number!r}")
    if lookup_bas_account(number) is None:
        raise VerificationTemplateCatalogError(f"Kontot {number} i {where} finns inte i BAS 2026")

    side = str(entry.get("side", "")).strip()
    if side not in _SIDES:
        raise VerificationTemplateCatalogError(f"Ogiltig sida i {where}: {side!r}")

    rule = str(entry.get("amount_rule", VerificationTemplateEntry.AmountRule.NONE)).strip()
    if rule not in _AMOUNT_RULES:
        raise VerificationTemplateCatalogError(f"Ogiltig beloppsregel i {where}: {rule!r}")

    percent = None
    if rule == VerificationTemplateEntry.AmountRule.PERCENT:
        try:
            percent = Decimal(str(entry["amount_percent"]))
        except (KeyError, InvalidOperation) as exc:
            raise VerificationTemplateCatalogError(f"Saknad eller ogiltig procentsats i {where}") from exc
        if percent <= 0:
            raise VerificationTemplateCatalogError(f"Procentsatsen i {where} måste vara positiv")
    elif "amount_percent" in entry and entry["amount_percent"] is not None:
        raise VerificationTemplateCatalogError(f"Procentsats angiven i {where} trots regeln {rule!r}")

    return {
        "account": number,
        "side": side,
        "amount_rule": rule,
        "amount_percent": percent,
    }


def _normalize_template(row):
    slug = str(row.get("slug", "")).strip()
    if not slug:
        raise VerificationTemplateCatalogError("Mall utan slug i mallkatalogen")

    name = str(row.get("name", "")).strip()
    if not name:
        raise VerificationTemplateCatalogError(f"Mallen {slug!r} saknar namn")

    raw_entries = row.get("entries") or []
    if len(raw_entries) < 2:
        raise VerificationTemplateCatalogError(f"Mallen {slug!r} måste ha minst två rader")

    entries = [_normalize_entry(entry, slug, index) for index, entry in enumerate(raw_entries)]

    remainders = sum(1 for entry in entries if entry["amount_rule"] == VerificationTemplateEntry.AmountRule.REMAINDER)
    if remainders > 1:
        raise VerificationTemplateCatalogError(f"Mallen {slug!r} har fler än en resterande-rad")

    _validate_balances(slug, entries)

    return {
        "slug": slug,
        "name": name,
        "description": str(row.get("description", "")).strip(),
        "category": str(row.get("category", "")).strip() or "Övrigt",
        "source_url": str(row.get("source_url", "")).strip(),
        "base_amount_label": str(row.get("base_amount_label", "")).strip(),
        "entries": entries,
    }


def _validate_balances(slug, entries):
    """En mall där varje rad har en regel måste balansera — även vid öresavrundning."""
    if any(entry["amount_rule"] == VerificationTemplateEntry.AmountRule.NONE for entry in entries):
        return

    for base in (Decimal("10000.00"), Decimal("3333.33")):
        amounts = compute_template_amounts(entries, base)
        debit = sum(
            (amount for entry, amount in zip(entries, amounts) if entry["side"] == "debit"),
            Decimal("0.00"),
        )
        credit = sum(
            (amount for entry, amount in zip(entries, amounts) if entry["side"] == "credit"),
            Decimal("0.00"),
        )
        if debit != credit:
            raise VerificationTemplateCatalogError(
                f"Mallen {slug!r} balanserar inte vid basbelopp {base}: debet {debit} ≠ kredit {credit}"
            )
        if any(amount < 0 for amount in amounts):
            raise VerificationTemplateCatalogError(f"Mallen {slug!r} ger ett negativt belopp vid basbelopp {base}")


@lru_cache(maxsize=1)
def load_template_catalog():
    if not _DATA_FILE.exists():
        raise VerificationTemplateCatalogError(f"Mallkatalogen saknas: {_DATA_FILE}")

    try:
        payload = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationTemplateCatalogError(f"Kunde inte tolka mallkatalogen: {_DATA_FILE}") from exc

    if not isinstance(payload, list) or not payload:
        raise VerificationTemplateCatalogError("Mallkatalogen innehåller inga mallar")

    templates = tuple(_normalize_template(row) for row in payload)

    slugs = [template["slug"] for template in templates]
    duplicates = {slug for slug in slugs if slugs.count(slug) > 1}
    if duplicates:
        raise VerificationTemplateCatalogError(f"Dubbletter av slug i mallkatalogen: {sorted(duplicates)}")

    # Namnen måste vara unika — VerificationTemplate har unique (company, name).
    names = [template["name"] for template in templates]
    duplicate_names = {name for name in names if names.count(name) > 1}
    if duplicate_names:
        raise VerificationTemplateCatalogError(f"Dubbletter av namn i mallkatalogen: {sorted(duplicate_names)}")

    return templates


@lru_cache(maxsize=1)
def _catalog_by_slug():
    return {template["slug"]: template for template in load_template_catalog()}


def lookup_catalog_template(slug):
    return _catalog_by_slug().get(str(slug).strip())


def catalog_by_category():
    """Katalogen grupperad på kategori, för biblioteksvyn."""
    grouped = {}
    for template in load_template_catalog():
        grouped.setdefault(template["category"], []).append(template)
    return sorted(grouped.items())


def import_catalog_templates_for_company(company, slugs):
    """Importera katalogmallar till ett företag.

    Matchar på ``(company, slug)`` så en omimport uppdaterar befintlig mall i stället för
    att skapa en dubblett. Mallar vars konton företaget saknar hoppas över.

    Returnerar ``(created, updated, skipped)`` där ``skipped`` är en lista av
    ``(slug, anledning)``.
    """
    created = 0
    updated = 0
    skipped = []

    for slug in slugs:
        template_data = lookup_catalog_template(slug)
        if template_data is None:
            skipped.append((slug, "Mallen finns inte i katalogen."))
            continue

        numbers = {entry["account"] for entry in template_data["entries"]}
        accounts = {account.number: account for account in Account.objects.filter(company=company, number__in=numbers)}
        missing = sorted(numbers - set(accounts))
        if missing:
            skipped.append((slug, f"Företaget saknar konto {', '.join(missing)}."))
            continue

        template = VerificationTemplate.objects.filter(company=company, slug=slug).first()
        name_taken = (
            VerificationTemplate.objects.filter(company=company, name=template_data["name"])
            .exclude(pk=template.pk if template else None)
            .exists()
        )
        if name_taken:
            skipped.append((slug, f"Det finns redan en egen mall som heter {template_data['name']!r}."))
            continue

        with db_transaction.atomic():
            if template is None:
                template = VerificationTemplate(company=company, slug=slug)
                created += 1
            else:
                updated += 1

            template.name = template_data["name"]
            template.description = template_data["description"]
            template.source_url = template_data["source_url"]
            template.base_amount_label = template_data["base_amount_label"]
            template.is_active = True
            template.save()

            template.entries.all().delete()
            VerificationTemplateEntry.objects.bulk_create(
                [
                    VerificationTemplateEntry(
                        template=template,
                        account=accounts[entry["account"]],
                        is_debit=entry["side"] == "debit",
                        is_credit=entry["side"] == "credit",
                        amount_rule=entry["amount_rule"],
                        amount_percent=entry["amount_percent"],
                        sort_order=index,
                    )
                    for index, entry in enumerate(template_data["entries"])
                ]
            )

    return created, updated, skipped
