import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from attachments.email_import import import_email_attachments_for_company
from bookkeeping.models import Company

logger = logging.getLogger(__name__)


def _record_fetch_error(company, error):
    """Persistera importfelstatus för notisklockan/digesten (bookkeeping/notifications.py).

    Sparar bara när status faktiskt ändras: Company är audit-trackad, och en
    skrivning var 15:e minut skulle spamma hashkedjan."""
    if error == company.email_fetch_last_error:
        return
    company.email_fetch_last_error = error
    company.email_fetch_last_error_at = timezone.now() if error else None
    company.save(update_fields=["email_fetch_last_error", "email_fetch_last_error_at"])


class Command(BaseCommand):
    help = (
        "Hämtar e-postbilagor för alla aktiva företag som har e-posthämtning påslagen. "
        "Avsedd att köras schemalagt (ofelia i docker-compose.yml, eller cron). "
        "Ett företag som misslyckas stoppar inte de övriga."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=int,
            default=None,
            help="Begränsa körningen till ett företags id.",
        )
        parser.add_argument(
            "--max-messages",
            type=int,
            default=100,
            help="Antal meddelanden att läsa per brevlåda (standard: 100).",
        )

    def handle(self, *args, **options):
        companies = Company.objects.filter(is_active=True, email_fetch_enabled=True).order_by("id")
        if options["company"] is not None:
            companies = companies.filter(pk=options["company"])

        if not companies.exists():
            self.stdout.write(self.style.WARNING("Inga företag har e-posthämtning påslagen."))
            return

        totals = {"imported": 0, "duplicates": 0, "skipped_unsupported": 0}
        failures = []

        for company in companies:
            try:
                # No request user on a scheduled run; uploaded_by stays empty rather
                # than crediting the import to whoever configured the company.
                result = import_email_attachments_for_company(
                    company=company,
                    user=None,
                    max_messages=options["max_messages"],
                )
            except Exception as exc:
                logger.exception("Scheduled email fetch failed", extra={"company_id": company.id})
                failures.append(f"{company.name}: {exc}")
                _record_fetch_error(company, str(exc)[:2000])
                continue

            _record_fetch_error(company, "")
            for key in totals:
                totals[key] += result[key]
            self.stdout.write(
                f"{company.name}: {result['imported']} importerade, "
                f"{result['duplicates']} dubbletter, "
                f"{result['skipped_unsupported']} ej stödda format."
            )

        summary = (
            f"Totalt: {totals['imported']} importerade, "
            f"{totals['duplicates']} dubbletter, "
            f"{totals['skipped_unsupported']} ej stödda format."
        )

        if failures:
            self.stdout.write(self.style.ERROR(f"{len(failures)} företag misslyckades:"))
            for failure in failures:
                self.stdout.write(self.style.ERROR(f"- {failure}"))
            self.stdout.write(summary)
            # Non-zero exit so the scheduler surfaces the failure instead of
            # silently succeeding while a mailbox has been broken for weeks.
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(summary))
