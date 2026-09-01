"""Registerutdrag per person (GDPR G-005, Art. 15/20).

Samlar alla rader i projektets appar som refererar subjektet (anställd, kund,
leverantör eller användare) plus auditloggposter, till JSON. Körs enligt
docs/compliance/gdpr/dsar-runbook.md.
"""

import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from auditlog.models import AuditLogEntry
from saldovibe.encryption import EncryptedTextField

LOCAL_APPS = [
    "accounts",
    "bookkeeping",
    "banking",
    "invoicing",
    "supplier_invoices",
    "expenses",
    "payroll",
    "vat",
    "attachments",
    "fixed_assets",
]

# Interna/hemliga fält som inte hör hemma i ett utdrag.
EXCLUDED_FIELDS = {"password", "personal_identity_number_hash"}

SUBJECT_MODELS = {
    "employee": "payroll.Employee",
    "customer": "invoicing.Customer",
    "supplier": "supplier_invoices.Supplier",
}


def _is_credential(field):
    # Krypterade inloggningsuppgifter (SMTP/OAuth på Company) ska inte med; krypterade
    # persondata (personnummer) ska det.
    return isinstance(field, EncryptedTextField) and ("password" in field.name or "secret" in field.name)


def dump_instance(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in EXCLUDED_FIELDS or _is_credential(field):
            continue
        value = getattr(instance, field.attname)
        data[field.name] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return data


def collect_subject_data(subject):
    """Alla rader i lokala appar som pekar på subjektet via FK eller M2M."""
    subject_model = type(subject)
    related = []
    audit_targets = [(subject._meta.label_lower, [str(subject.pk)])]

    for model in apps.get_models():
        if model._meta.app_label not in LOCAL_APPS:
            continue
        for field in model._meta.get_fields():
            if not (getattr(field, "is_relation", False) and field.related_model is subject_model):
                continue
            if not (field.many_to_one or field.one_to_one or field.many_to_many):
                continue
            if not field.concrete:
                continue
            rows = model.objects.filter(**{field.name: subject})
            dumped = [dump_instance(row) for row in rows]
            if not dumped:
                continue
            related.append(
                {
                    "model": model._meta.label_lower,
                    "via_field": field.name,
                    "rows": dumped,
                }
            )
            if not field.many_to_many:
                # M2M-träffar (t.ex. företag användaren tillhör) är medlemskap,
                # inte subjektets egna rader — deras audithistorik hör inte hit.
                audit_targets.append((model._meta.label_lower, [str(row["id"]) for row in dumped]))

    audit_query = Q()
    for label, pks in audit_targets:
        audit_query |= Q(model_label=label, object_pk__in=pks)
    audit_entries = [
        {
            "occurred_at": str(entry.occurred_at),
            "action": entry.action,
            "model": entry.model_label,
            "object": entry.object_repr,
            "summary": entry.summary,
            "changes": entry.changes,
        }
        for entry in AuditLogEntry.objects.filter(audit_query).order_by("occurred_at")
    ]

    return {
        "subject_model": subject._meta.label_lower,
        "subject": dump_instance(subject),
        "related": related,
        "audit_entries": audit_entries,
    }


class Command(BaseCommand):
    help = "Exportera ett registerutdrag (DSAR, Art. 15) för en person som JSON."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--employee", type=int, help="Anställd (payroll.Employee pk)")
        group.add_argument("--customer", type=int, help="Kund (invoicing.Customer pk)")
        group.add_argument("--supplier", type=int, help="Leverantör (supplier_invoices.Supplier pk)")
        group.add_argument("--user", help="Användare (e-postadress)")
        parser.add_argument("--output", help="Skriv till fil i stället för stdout")

    def handle(self, *args, **options):
        subject = self._resolve_subject(options)
        result = collect_subject_data(subject)

        if options["user"]:
            # Användarens egen aktivitet: metadata, inte ändringsinnehållet —
            # changes-fälten rör företagets data, inte användarens persondata.
            result["audit_entries_as_actor"] = [
                {
                    "occurred_at": str(entry.occurred_at),
                    "action": entry.action,
                    "model": entry.model_label,
                    "object": entry.object_repr,
                    "summary": entry.summary,
                }
                for entry in subject.audit_log_entries.order_by("occurred_at")
            ]

        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as f:
                f.write(payload)
            self.stdout.write(f"Utdrag skrivet till {options['output']}")
        else:
            self.stdout.write(payload)

    def _resolve_subject(self, options):
        for key, label in SUBJECT_MODELS.items():
            if options[key]:
                model = apps.get_model(label)
                try:
                    return model.objects.get(pk=options[key])
                except model.DoesNotExist:
                    raise CommandError(f"{label} med pk {options[key]} finns inte.")
        user_model = apps.get_model("accounts.CustomUser")
        try:
            return user_model.objects.get(email=options["user"])
        except user_model.DoesNotExist:
            raise CommandError(f"Ingen användare med e-post {options['user']}.")
