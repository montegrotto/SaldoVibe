"""Management command: populate_sru_codes

Backfill/repair BAS account SRU codes for the INK2 form (Inkomstdeklaration 2 -
aktiebolag / ekonomisk förening). New accounts get their code automatically on
creation via Account.save() (see bookkeeping/sru_lookup.py); this command exists
to backfill accounts created before that, or to repair codes after a mapping
change (--overwrite).

Safe to run multiple times - skips accounts that already have a code unless
--overwrite is passed.
"""

from django.core.management.base import BaseCommand

from bookkeeping.models import Account
from bookkeeping.sru_lookup import resolve_sru_code


class Command(BaseCommand):
    help = "Fyll i SRU-koder på BAS-konton baserat på standardmapping (INK2)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            metavar="NAME_OR_ID",
            help="Begränsa till ett specifikt företag (namn eller ID). Standard: alla företag.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            default=False,
            help="Skriv över befintliga SRU-koder. Standard: hoppa över konton som redan har en kod.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Visa vad som skulle ändras utan att spara.",
        )

    def handle(self, *args, **options):
        qs = Account.objects.all()

        company_arg = options.get("company")
        if company_arg:
            if company_arg.isdigit():
                qs = qs.filter(company_id=int(company_arg))
            else:
                qs = qs.filter(company__name__icontains=company_arg)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f"Inga konton hittades för '{company_arg}'."))
                return

        overwrite = options["overwrite"]
        dry_run = options["dry_run"]

        updated = 0
        skipped_has_code = 0
        skipped_no_match = 0

        accounts_to_update = []

        for account in qs.order_by("company_id", "number"):
            if account.sru_code and not overwrite:
                skipped_has_code += 1
                continue

            new_code = resolve_sru_code(account.number, account.name)
            if not new_code:
                skipped_no_match += 1
                continue

            if account.sru_code == new_code:
                continue

            if dry_run:
                self.stdout.write(
                    f"  [DRY-RUN] {account.number} {account.name}: {account.sru_code or '(tom)'} → {new_code}"
                )
            else:
                account.sru_code = new_code
                accounts_to_update.append(account)
            updated += 1

        if not dry_run and accounts_to_update:
            Account.objects.bulk_update(accounts_to_update, ["sru_code"])

        label = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{label}Klart: {updated} konton uppdaterade, "
                f"{skipped_has_code} hoppade över (hade redan kod), "
                f"{skipped_no_match} utan matchande SRU-intervall."
            )
        )
