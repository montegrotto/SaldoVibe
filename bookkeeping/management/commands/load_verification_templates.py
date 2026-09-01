"""Management command: load_verification_templates

Importera standardmallarna i bookkeeping/data/verification_templates.json till ett
eller flera företag. Mallar matchas på (company, slug), så en omkörning uppdaterar
befintliga katalogmallar i stället för att skapa dubbletter.

Egenskapade mallar (utan slug) rörs aldrig — inte ens med --replace, som bara rensar
katalogmallar som inte längre finns i katalogen.

Mallar vars konton företaget saknar hoppas över; inget importeras delvis.
"""

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from bookkeeping.models import Company, VerificationTemplate
from bookkeeping.verification_template_catalog import (
    import_catalog_templates_for_company,
    load_template_catalog,
)


class Command(BaseCommand):
    help = "Importera standardmallar för verifikationer till företagens mallbibliotek"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            metavar="NAME_OR_ID",
            help="Begränsa till ett specifikt företag (namn eller ID). Standard: alla företag.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            default=False,
            help="Ta bort katalogmallar som inte längre finns i katalogen. Egenskapade mallar rörs inte.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Visa vad som skulle ändras utan att spara.",
        )

    def handle(self, *args, **options):
        companies = Company.objects.all().order_by("name")

        company_arg = options.get("company")
        if company_arg:
            if company_arg.isdigit():
                companies = companies.filter(pk=int(company_arg))
            else:
                companies = companies.filter(name__icontains=company_arg)
            if not companies.exists():
                self.stderr.write(self.style.ERROR(f"Inga företag hittades för '{company_arg}'."))
                return

        catalog = load_template_catalog()
        slugs = [template["slug"] for template in catalog]
        replace = options["replace"]
        dry_run = options["dry_run"]
        prefix = "[DRY-RUN] " if dry_run else ""

        self.stdout.write(f"{prefix}Katalog: {len(catalog)} mallar\n")

        total_created = 0
        total_updated = 0
        total_removed = 0
        total_skipped = 0

        for company in companies:
            kept_custom = VerificationTemplate.objects.filter(company=company, slug="").count()

            if dry_run:
                created, updated, removed, skipped = self._preview(company, slugs, replace)
            else:
                with db_transaction.atomic():
                    removed = self._remove_stale(company, slugs) if replace else 0
                    created, updated, skipped = import_catalog_templates_for_company(company, slugs)

            total_created += created
            total_updated += updated
            total_removed += removed
            total_skipped += len(skipped)

            parts = [f"{created} nya", f"{updated} uppdaterade"]
            if replace:
                parts.append(f"{removed} borttagna")
            if skipped:
                parts.append(f"{len(skipped)} överhoppade")
            if kept_custom:
                parts.append(f"{kept_custom} egna orörda")

            self.stdout.write(f"{prefix}{company.name}: {', '.join(parts)}")

            if skipped and options["verbosity"] >= 2:
                for slug, reason in skipped:
                    self.stdout.write(f"    - {slug}: {reason}")

        self.stdout.write("")
        summary = f"{total_created} nya, {total_updated} uppdaterade"
        if replace:
            summary += f", {total_removed} borttagna"
        summary += f", {total_skipped} överhoppade"
        self.stdout.write(self.style.SUCCESS(f"{prefix}Klart: {summary}."))

        if total_skipped and options["verbosity"] < 2:
            self.stdout.write("Kör med -v2 för att se varför mallar hoppades över.")

    def _remove_stale(self, company, slugs):
        """Ta bort katalogmallar vars slug inte längre finns i katalogen."""
        stale = VerificationTemplate.objects.filter(company=company).exclude(slug="").exclude(slug__in=slugs)
        count = stale.count()
        for template in stale:
            template.entries.all().delete()
        stale.delete()
        return count

    def _preview(self, company, slugs, replace):
        from bookkeeping.models import Account

        existing = set(
            VerificationTemplate.objects.filter(company=company).exclude(slug="").values_list("slug", flat=True)
        )
        numbers = set(Account.objects.filter(company=company).values_list("number", flat=True))

        created = updated = 0
        skipped = []
        for template in load_template_catalog():
            missing = sorted({entry["account"] for entry in template["entries"]} - numbers)
            if missing:
                skipped.append((template["slug"], f"Företaget saknar konto {', '.join(missing)}."))
            elif template["slug"] in existing:
                updated += 1
            else:
                created += 1

        removed = len(existing - set(slugs)) if replace else 0
        return created, updated, removed, skipped
