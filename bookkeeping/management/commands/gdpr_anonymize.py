"""Anonymisering av persondata utan lagkrav (GDPR G-006, Art. 17).

Anonymiserar i stället för att radera där främmande nycklar eller auditkedjan
refererar raden. Räkenskapsinformation rörs inte (Art. 17(3)(b), se
docs/compliance/gdpr/retention-schedule.md). Varje körning loggas till
<DATA_DIR>/gdpr-erasures.jsonl så raderingar kan återappliceras efter en
databasåterställning (G-014). Körs enligt docs/compliance/gdpr/dsar-runbook.md;
åtgärden motsvarar compliance-åtgärden ``gdpr.erase`` (finance_admin).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Fält som blankas per subjektstyp. Namn behålls: fakturor/lönebesked är
# räkenskapsinformation och motparten måste förbli identifierbar under
# 7-årsfristen.
CONTACT_FIELDS = {
    "payroll.Employee": ["address", "postal_code", "city"],
    "invoicing.Customer": ["email", "phone", "address", "postal_code", "city"],
    "supplier_invoices.Supplier": ["email", "phone"],
}


class Command(BaseCommand):
    help = "Anonymisera en användare eller kontaktuppgifter för anställd/kund/leverantör (GDPR Art. 17)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--employee", type=int, help="Anställd (payroll.Employee pk)")
        group.add_argument("--customer", type=int, help="Kund (invoicing.Customer pk)")
        group.add_argument("--supplier", type=int, help="Leverantör (supplier_invoices.Supplier pk)")
        group.add_argument("--user", help="Användare (e-postadress)")
        parser.add_argument("--yes", action="store_true", help="Bekräfta anonymiseringen")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Anonymisering är oåterkallelig — kör igen med --yes för att bekräfta.")

        if options["user"]:
            record = self._anonymize_user(options["user"])
        else:
            record = self._anonymize_contact_fields(options)

        self._append_erasure_log(record)
        self.stdout.write(self.style.SUCCESS(f"Anonymiserade {record['subject']} pk={record['pk']}."))

    def _anonymize_user(self, email):
        user_model = apps.get_model("accounts.CustomUser")
        try:
            user = user_model.objects.get(email=email)
        except user_model.DoesNotExist:
            raise CommandError(f"Ingen användare med e-post {email}.")

        user.email = f"raderad-anvandare-{user.pk}@example.invalid"
        user.first_name = ""
        user.last_name = ""
        user.is_active = False
        user.set_unusable_password()
        user.save()
        return {
            "subject": "accounts.customuser",
            "pk": user.pk,
            "action": "user_anonymized",
        }

    def _anonymize_contact_fields(self, options):
        for key, label in (
            ("employee", "payroll.Employee"),
            ("customer", "invoicing.Customer"),
            ("supplier", "supplier_invoices.Supplier"),
        ):
            if options[key]:
                model = apps.get_model(label)
                try:
                    instance = model.objects.get(pk=options[key])
                except model.DoesNotExist:
                    raise CommandError(f"{label} med pk {options[key]} finns inte.")
                fields = CONTACT_FIELDS[label]
                for field in fields:
                    setattr(instance, field, "")
                instance.save(update_fields=fields)
                return {
                    "subject": label.lower(),
                    "pk": instance.pk,
                    "action": "contact_fields_cleared",
                    "fields": fields,
                }
        raise CommandError("Inget subjekt angivet.")

    def _append_erasure_log(self, record):
        record["anonymized_at"] = datetime.now(timezone.utc).isoformat()
        log_path = Path(settings.DATA_DIR) / "gdpr-erasures.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
