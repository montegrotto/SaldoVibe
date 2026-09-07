---
description: "Hur SaldoVibe kör databasmigrationer vid uppgradering, och hur man rullar tillbaka."
---

# Uppgraderingar & migrationer

## Hur migrationer körs i dag

`scripts/docker-entrypoint.sh` körs vid varje start av `web`-containern:

```sh
if [ "${SALDOVIBE_RUN_MIGRATIONS:-1}" = "1" ]; then
    /opt/venv/bin/python /app/manage.py migrate --noinput
fi
```

Det betyder att **varje omstart av `web`-containern automatiskt tillämpar väntande migrationer**,
även under en rutindeploy (se [deploy-checklist.md](deploy-checklist.md)). Det finns inget separat
"kör migrationer"-steg att komma ihåg för vanliga releaser.

Sätt `SALDOVIBE_RUN_MIGRATIONS=0` i `.env` om du någon gång vill frikoppla migrationerna från
containerstarten (t.ex. köra `migrate` manuellt i ett servicefönster innan den nya `web`-imagen
startas).

## Köra migrationer manuellt

```bash
docker compose exec web python manage.py migrate
```

För att förhandsgranska vad en deploy skulle ändra utan att tillämpa det:

```bash
docker compose exec web python manage.py migrate --plan
```

För att kontrollera att inga modelländringar saknar migrationsfil (användbart i CI eller före
merge):

```bash
docker compose exec web python manage.py makemigrations --check --dry-run
```

## Före en migration som rör bokföringstabeller

Migrationer som påverkar modeller i `bookkeeping`, `banking`, `payroll`, `vat`, `invoicing`,
`supplier_invoices`, `fixed_assets` eller `auditlog` innebär större risk än i en typisk Django-app
på grund av de compliance-krav som redan upprätthålls i applikationslagret (balanserade
verifikationer, verifikationsnumrering, periodlås, revisionsloggens hashkedja — se
`docs/compliance/` och `docs/system-replication-spec.md` avsnitt 5). Innan en sådan migration
driftsätts:

1. Ta en färsk PostgreSQL-backup (se [backup-restore.md](backup-restore.md)).
2. Läs migrationsfilen, inte bara modell-diffen — Djangos autogenererade migrationer kan välja
   överraskande standardvärden för nya icke-nullbara fält på tabeller som redan har rader.
3. Om migrationen ändrar något i de revisionsloggade modellerna: kör
   `python manage.py verify_audit_chain` **efter** deployen för att bekräfta att hashkedjan inte
   rubbats (t.ex. av en datamigration som rör loggade fält direkt i stället för via den vanliga
   modell-/signalvägen).

## Rulla tillbaka

Django-migrationer kan backas om migrationen har en fungerande `reverse`-operation (de flesta
autogenererade schemamigrationer har det; handskrivna datamigrationer kanske inte):

```bash
docker compose exec web python manage.py migrate <app_label> <föregående_migrationsnamn>
```

Fallgropar:

- Att backa **schemat** ångrar inte **dataändringar** en migration kan ha gjort (t.ex. en
  datamigration som fyllt i ett nytt fält). Leta efter ett `RunPython`-steg i migrationsfilen
  innan du antar att en rollback är ren.
- Vid tveksamhet: återställ hellre PostgreSQL-backupen från före deployen än att försöka
  bakåtmigrera en produktionsdatabas — se [backup-restore.md](backup-restore.md).
- Rulla tillbaka `web`-containerns image tillsammans med schemat — att köra ny kod mot ett gammalt
  schema (eller tvärtom) är en vanligare felkälla än själva migrationen.

## Relaterade management-kommandon

| Kommando | Syfte |
|---|---|
| `manage.py migrate` | Tillämpa/backa schemamigrationer. |
| `manage.py makemigrations --check --dry-run` | Verifiera att inga modelländringar saknar migration. |
| `manage.py verify_audit_chain` | Bekräfta att revisionsloggens hashkedja är intakt efter deploy/migration. |
| `manage.py reseal_audit_chain` | Räkna om `prev_hash`/`entry_hash` (torrkörning som standard, `--apply` för att spara) — bara för avsiktliga, förstådda reparationer, inte rutinbruk. |
| `manage.py load_bas_accounts` | Ladda (om) BAS-kontoplansfixturen som används när nya företag skapas. |
| `manage.py populate_sru_codes` | Fyll i SRU-koder på befintliga konton. |
