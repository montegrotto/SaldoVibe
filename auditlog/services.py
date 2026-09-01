import hashlib
import hmac
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import models
from django.db import transaction as db_transaction
from django.db.models.fields.files import FieldFile

from .context import get_current_source, get_current_user
from .models import AuditChainTip, AuditLogEntry

TRACKED_MODELS = {
    "bookkeeping.company": {
        "name": "Företag",
        "company_path": None,
        "sensitive_fields": {
            "email_fetch_password",
            "email_fetch_oauth_client_secret",
            "email_send_smtp_password",
            "email_notify_smtp_password",
        },
    },
    "bookkeeping.account": {"name": "Konto", "company_path": "company"},
    "bookkeeping.accountingyear": {"name": "Räkenskapsår", "company_path": "company"},
    "bookkeeping.transaction": {"name": "Verifikation", "company_path": "accounting_year.company"},
    "bookkeeping.journalentry": {"name": "Konteringsrad", "company_path": "transaction.accounting_year.company"},
    "bookkeeping.voucherseriesrule": {"name": "Verifikationsserieregel", "company_path": "company"},
    "bookkeeping.periodlock": {"name": "Periodlås", "company_path": "company"},
    "bookkeeping.budgetline": {"name": "Budgetrad", "company_path": "company"},
    "banking.bankaccount": {"name": "Bankkälla", "company_path": "company"},
    "banking.bankimport": {"name": "Bankimport", "company_path": "company"},
    "banking.banktransaction": {"name": "Banktransaktion", "company_path": "company"},
    "invoicing.customer": {"name": "Kund", "company_path": "company"},
    "invoicing.article": {"name": "Artikel", "company_path": "company"},
    "invoicing.invoice": {"name": "Kundfaktura", "company_path": "company"},
    "invoicing.invoiceline": {"name": "Fakturarad", "company_path": "invoice.company"},
    "invoicing.invoicepayment": {"name": "Fakturabetalning", "company_path": "payable.company"},
    "invoicing.invoicereminder": {"name": "Betalningspåminnelse", "company_path": "invoice.company"},
    "supplier_invoices.supplier": {"name": "Leverantör", "company_path": "company"},
    "supplier_invoices.supplierinvoice": {"name": "Leverantörsfaktura", "company_path": "company"},
    "supplier_invoices.supplierinvoicecostline": {
        "name": "Kostnadsrad leverantörsfaktura",
        "company_path": "invoice.company",
    },
    "supplier_invoices.supplierinvoicepayment": {
        "name": "Leverantörsfakturabetalning",
        "company_path": "payable.company",
    },
    "expenses.expenseclaim": {"name": "Utlägg", "company_path": "company"},
    "expenses.expenseclaimpayment": {"name": "Utläggsbetalning", "company_path": "payable.company"},
    "payroll.employee": {
        "name": "Anställd",
        "company_path": "company",
        "sensitive_fields": {"personal_identity_number", "personal_identity_number_hash"},
    },
    "payroll.payrollrun": {"name": "Lönekörning", "company_path": "company"},
    "payroll.salaryrecord": {"name": "Lönebesked", "company_path": "payroll_run.company"},
    "payroll.payrollreportevidence": {"name": "AGI-bevispaket", "company_path": "payroll_run.company"},
    "payroll.salaryadjustment": {
        "name": "Lönejustering",
        "company_path": "salary_record.payroll_run.company",
    },
    "vat.vatclosesnapshot": {"name": "Momsstängning", "company_path": "company"},
    "attachments.transactionattachment": {"name": "Bilaga", "company_path": "company"},
    "fixed_assets.fixedassettype": {"name": "Tillgångstyp", "company_path": "company"},
    "fixed_assets.fixedasset": {"name": "Anläggningstillgång", "company_path": "company"},
    "fixed_assets.fixedassetdepreciation": {
        "name": "Avskrivning",
        "company_path": "fixed_asset.company",
    },
    "fixed_assets.fixedassetimpairment": {
        "name": "Nedskrivning",
        "company_path": "fixed_asset.company",
    },
    "fixed_assets.fixedassetreclassification": {
        "name": "Omklassificering",
        "company_path": "fixed_asset.company",
    },
}

CHILD_PARENT_RELATIONS = {
    "bookkeeping.journalentry": {"field": "transaction", "parent_label": "bookkeeping.transaction"},
    "invoicing.invoiceline": {"field": "invoice", "parent_label": "invoicing.invoice"},
    "invoicing.invoicepayment": {"field": "payable", "parent_label": "invoicing.invoice"},
    "invoicing.invoicereminder": {"field": "invoice", "parent_label": "invoicing.invoice"},
    "supplier_invoices.supplierinvoicecostline": {
        "field": "invoice",
        "parent_label": "supplier_invoices.supplierinvoice",
    },
    "supplier_invoices.supplierinvoicepayment": {
        "field": "payable",
        "parent_label": "supplier_invoices.supplierinvoice",
    },
    "expenses.expenseclaimpayment": {"field": "payable", "parent_label": "expenses.expenseclaim"},
    "payroll.salaryrecord": {"field": "payroll_run", "parent_label": "payroll.payrollrun"},
    "payroll.salaryadjustment": {"field": "salary_record", "parent_label": "payroll.salaryrecord"},
    "fixed_assets.fixedassetdepreciation": {
        "field": "fixed_asset",
        "parent_label": "fixed_assets.fixedasset",
    },
    "fixed_assets.fixedassetimpairment": {
        "field": "fixed_asset",
        "parent_label": "fixed_assets.fixedasset",
    },
    "fixed_assets.fixedassetreclassification": {
        "field": "fixed_asset",
        "parent_label": "fixed_assets.fixedasset",
    },
}

DEFAULT_EXCLUDED_FIELDS = {"id", "created_at", "updated_at"}
ACTOR_FIELDS = ("created_by", "imported_by", "uploaded_by")
ACTION_SUMMARY_PREFIX = {
    AuditLogEntry.Action.CREATE: "Skapad",
    AuditLogEntry.Action.UPDATE: "Uppdaterad",
    AuditLogEntry.Action.DELETE: "Raderad",
}


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_audit_entry_hash(entry, prev_hash):
    payload = {
        "id": entry.id,
        "occurred_at": entry.occurred_at.isoformat() if entry.occurred_at else "",
        "action": entry.action,
        "actor_display": entry.actor_display,
        "company_name": entry.company_name,
        "model_label": entry.model_label,
        "model_name": entry.model_name,
        "object_pk": entry.object_pk,
        "object_repr": entry.object_repr,
        "summary": entry.summary,
        "changes": entry.changes,
        "metadata": entry.metadata,
        "hash_version": entry.hash_version,
        "prev_hash": prev_hash,
    }
    # hash_version 1 entries were sealed before chain_key existed; adding it to their
    # payload would invalidate every historical hash.
    if entry.hash_version >= 2:
        payload["chain_key"] = entry.chain_key
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def get_model_label(instance):
    return instance._meta.label_lower


def is_tracked_model(instance):
    return get_model_label(instance) in TRACKED_MODELS


def get_tracked_models():
    return tuple(TRACKED_MODELS.keys())


def get_model_config(instance):
    return TRACKED_MODELS[get_model_label(instance)]


def resolve_attr(instance, dotted_path):
    current = instance
    for part in dotted_path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


def get_company_for_instance(instance):
    config = get_model_config(instance)
    company_path = config.get("company_path")
    if not company_path:
        return instance
    return resolve_attr(instance, company_path)


def normalize_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, FieldFile):
        return value.name or ""
    if isinstance(value, dict):
        return {key: normalize_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    if isinstance(value, models.Model):
        return str(value)
    return value


def get_field_display_value(instance, field):
    if field.is_relation and field.many_to_one:
        related_object = getattr(instance, field.name, None)
        if related_object is None:
            return None
        return normalize_value(related_object)
    return normalize_value(field.value_from_object(instance))


def snapshot_instance(instance):
    config = get_model_config(instance)
    sensitive_fields = config.get("sensitive_fields", set())
    snapshot = {}
    field_labels = {}

    for field in instance._meta.concrete_fields:
        if field.name in DEFAULT_EXCLUDED_FIELDS:
            continue
        value = get_field_display_value(instance, field)
        if field.name in sensitive_fields and value not in (None, ""):
            # Keyed digest so a changed secret still shows up as a change in the
            # log, without the value (or an enumerable personnummer hash) leaking.
            digest = hmac.new(settings.SECRET_KEY.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:12]
            normalized = f"[redacted:{digest}]"
        else:
            normalized = value
        snapshot[field.name] = normalized
        field_labels[field.name] = str(field.verbose_name)

    return snapshot, field_labels


def build_changes(before, after):
    changes = {}
    all_fields = sorted(set(before.keys()) | set(after.keys()))
    for field_name in all_fields:
        previous = before.get(field_name)
        current = after.get(field_name)
        if previous == current:
            continue
        changes[field_name] = {
            "before": previous,
            "after": current,
        }
    return changes


def build_create_changes(after):
    return {
        field_name: {"before": None, "after": value}
        for field_name, value in after.items()
        if value not in (None, "", [], {})
    }


def build_delete_changes(before):
    return {
        field_name: {"before": value, "after": None}
        for field_name, value in before.items()
        if value not in (None, "", [], {})
    }


def resolve_actor(instance):
    actor = get_current_user()
    if actor is not None:
        return actor

    for field_name in ACTOR_FIELDS:
        actor = getattr(instance, field_name, None)
        if actor is not None:
            return actor
    return None


def build_actor_display(actor):
    if actor is None:
        return "System"
    full_name = actor.get_full_name() if hasattr(actor, "get_full_name") else ""
    return full_name or getattr(actor, "email", "") or getattr(actor, "username", "") or str(actor.pk)


def create_audit_log(instance, action, *, before=None, after=None, field_labels=None):
    if not is_tracked_model(instance):
        return

    company = get_company_for_instance(instance)
    actor = resolve_actor(instance)
    config = get_model_config(instance)
    model_name = config["name"]

    if action == AuditLogEntry.Action.CREATE:
        changes = build_create_changes(after or {})
    elif action == AuditLogEntry.Action.DELETE:
        changes = build_delete_changes(before or {})
    else:
        changes = build_changes(before or {}, after or {})

    if action == AuditLogEntry.Action.UPDATE and not changes:
        return

    source = get_current_source()
    field_labels_map = dict(field_labels or {})

    if source and action == AuditLogEntry.Action.CREATE:
        changes["_audit_source"] = {"before": None, "after": source}
        field_labels_map["_audit_source"] = "Källa"

    metadata = {
        "field_labels": field_labels_map,
    }
    if source:
        metadata["source"] = source
    relation_config = CHILD_PARENT_RELATIONS.get(get_model_label(instance))
    if relation_config:
        parent = getattr(instance, relation_config["field"], None)
        if parent is not None:
            metadata["parent"] = {
                "model_label": relation_config["parent_label"],
                "object_pk": str(parent.pk),
                "object_repr": str(parent),
            }

    # One hash chain per company (chain_key = company pk, "" for company-less entries).
    # hash_version=1 entries predate this and form a frozen global legacy chain, verified
    # separately by verify_audit_chain.
    chain_key = str(company.pk) if company is not None else ""

    with db_transaction.atomic():
        # Serialize writers on the same chain via a dedicated lock row: FOR UPDATE on the
        # chain's last *entry* is racy under READ COMMITTED (a concurrent writer's newly
        # committed entry is invisible after the lock wait, so both chain to the same
        # prev_hash). With the tip row locked, the read below always sees the latest
        # committed entry.
        AuditChainTip.objects.get_or_create(chain_key=chain_key)
        AuditChainTip.objects.select_for_update().get(chain_key=chain_key)

        previous_entry = AuditLogEntry.objects.filter(hash_version__gte=2, chain_key=chain_key).order_by("-id").first()
        prev_hash = previous_entry.entry_hash if previous_entry is not None else ""

        entry = AuditLogEntry.objects.create(
            action=action,
            actor=actor,
            actor_display=build_actor_display(actor),
            company=company,
            company_name=getattr(company, "name", "") if company is not None else "",
            model_label=get_model_label(instance),
            model_name=model_name,
            object_pk=str(instance.pk),
            object_repr=str(instance),
            summary=(
                f"{ACTION_SUMMARY_PREFIX[action]}: {model_name} {instance}" + (f" (via {source})" if source else "")
            ),
            changes=changes,
            metadata=metadata,
            hash_version=2,
            chain_key=chain_key,
            prev_hash=prev_hash,
        )

        entry_hash = calculate_audit_entry_hash(entry, prev_hash)
        AuditLogEntry.objects.filter(pk=entry.pk).update(entry_hash=entry_hash)
