import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookkeeping.models import Company, SentEmail
from bookkeeping.notifications import (
    get_failed_job_state,
    get_overdue_customer_invoice_state,
    get_overdue_supplier_invoice_state,
    get_vat_deadline_state,
)
from bookkeeping.outgoing_mail import send_system_email

logger = logging.getLogger(__name__)

SECTIONS = (
    ("Förfallna kundfakturor", get_overdue_customer_invoice_state),
    ("Förfallna leverantörsfakturor", get_overdue_supplier_invoice_state),
    ("Momsdeklarationer", get_vat_deadline_state),
    ("Misslyckade e-postjobb", get_failed_job_state),
)


def build_digest_body(company, today):
    """Svensk plain-text-sammanfattning, eller "" när inget behöver uppmärksamhet.

    Daglig kadens är dedup:en — samma rader kan återkomma tills de är åtgärdade."""
    sections = []
    for heading, get_state in SECTIONS:
        lines, count, _ = get_state(company, today)
        if count:
            sections.append(heading + "\n" + "\n".join(f"- {line}" for line in lines))
    if not sections:
        return ""
    intro = f"Hej!\n\nFöljande behöver uppmärksamhet i {company.name}:\n\n"
    return intro + "\n\n".join(sections) + "\n\nHälsningar\nSaldoVibe"


class Command(BaseCommand):
    help = (
        "Skickar en daglig e-postsammanfattning per företag till företagets användare "
        "när något behöver uppmärksamhet (samma källor som notisklockan). "
        "Avsedd att köras schemalagt (ofelia). Ett företag som misslyckas stoppar inte de övriga."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, default=None, help="Begränsa körningen till ett företags id.")

    def handle(self, *args, **options):
        companies = Company.objects.filter(is_active=True).order_by("id")
        if options["company"] is not None:
            companies = companies.filter(pk=options["company"])

        today = timezone.localdate()
        failures = []
        sent_count = 0

        for company in companies:
            try:
                body = build_digest_body(company, today)
                if not body:
                    continue
                subject = f"SaldoVibe: påminnelser för {company.name} {today}"
                for user in company.users.filter(is_active=True):
                    result = send_system_email(
                        company,
                        purpose=SentEmail.Purpose.DIGEST,
                        to=[user.email],
                        subject=subject,
                        body=body,
                    )
                    if result.status == SentEmail.Status.SENT:
                        sent_count += 1
                    else:
                        failures.append(f"{company.name} -> {user.email}: {result.error}")
            except Exception as exc:
                logger.exception("Notification digest failed", extra={"company_id": company.id})
                failures.append(f"{company.name}: {exc}")

        summary = f"Totalt: {sent_count} digestmail skickade."
        if failures:
            self.stdout.write(self.style.ERROR(f"{len(failures)} utskick misslyckades:"))
            for failure in failures:
                self.stdout.write(self.style.ERROR(f"- {failure}"))
            self.stdout.write(summary)
            # Non-zero exit så schemaläggaren ser felet i stället för att tyst lyckas.
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(summary))
