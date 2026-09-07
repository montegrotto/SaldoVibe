---
description: "Backup och återställning av SaldoVibes produktionsstack: PostgreSQL- och mediavolymer."
---

# Backup & återställning i produktion

Det här handlar om backup/återställning på **infrastrukturnivå** för `docker-compose.yml`-stacken
(PostgreSQL + media-/static-volymer). Det är skilt från
[`docs/compliance/restore-runbook.md`](../compliance/restore-runbook.md), som bara övar på
**sqlite**-databasen för dev/desktop som en torrkörning för revisionsbevis (management-kommandot
`compliance_restore_dry_run`) — det kommandot läser `settings.DATABASES['default']['NAME']` som en
sqlite-filsökväg och fungerar inte alls mot PostgreSQL.

## Vad som behöver säkerhetskopieras

| Data | Var det ligger i prod-stacken | Konsekvens vid förlust |
|---|---|---|
| PostgreSQL-databasen | Volymen `saldovibe-postgres` (via tjänsten `db`) | All bokföringsdata, användare, företag |
| Uppladdad media (bilagor, fakturor, företagsloggor) | Volymen `media-assets`, monterad på `/data/media` i `web` | Underlag/bilagor som bokförda poster hänvisar till |
| Statiska filer | Volymen `static-assets` | Kan återskapas med `collectstatic` — inte kritiskt att backa upp |
| `.env` | Git-ignorerad fil på hosten | Utan den kan stackens secrets/konfiguration inte återskapas. **Täcks inte av ofelia-jobben** (containrarna ser den bara som miljövariabler) — off-host-synken nedan måste ta med den |

## Schemalagda backuper (inbyggda i stacken)

Tjänsten `scheduler` (ofelia) i `docker-compose.yml` kör backuperna automatiskt — ingen cron på
hosten behövs för att ta dem:

| Jobb | Var | Schema | Sparas |
|---|---|---|---|
| `nightly-pg-backup` | labels på `db` | 02:30 varje natt | 14 dagar |
| `weekly-media-backup` | labels på `web` | Söndag 04:00 | 35 dagar |

Scheman och tidsstämplarna i filnamnen är svensk lokal tid (`TZ=Europe/Stockholm` på `web`, `db`
och `scheduler` i compose-filen), så en host-cron som synkar dem off-host kan tidsättas mot samma
klocka.

Båda skriver till `./backups/` bredvid compose-filen (bind-monterad som `/backups` i `db` och
`web`): `db-<tidsstämpel>.dump` (`pg_dump --format=custom`, fungerar med `pg_restore` och stödjer
selektiv/parallell återställning) och `media-<tidsstämpel>.tar.gz`. Inloggningsuppgifterna kommer
från `.env` (`POSTGRES_USER` / `POSTGRES_DB`, se [environment-variables.md](environment-variables.md)).

Ofelia loggar varje körning — kontrollera att jobben verkligen går efter en deploy:

```bash
docker compose logs scheduler
```

**Att synka bort från hosten är fortfarande ditt jobb.** En backup som bara ligger på samma disk
som databasen är ingen backup. En cron-rad på hosten med t.ex. `rclone` räcker, inklusive `.env`
(som ofelia-jobben inte kommer åt):

```cron
0 7 * * * rclone sync /path/to/saldovibe/backups remote:saldovibe-backups/backups && rclone copy /path/to/saldovibe/.env remote:saldovibe-backups/env/
```

`.env` innehåller secrets i klartext — peka `remote:` mot en krypterad remote (rclones `crypt`-typ
som omsluter lagringsremoten) eller åtminstone en bucket som bara du kan läsa.

För en manuell dump på begäran (t.ex. precis före en riskabel migration):

```bash
docker compose exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > backups/db-$(date +%Y%m%dT%H%M%S).dump
```

## Återställa PostgreSQL

1. Stoppa tjänsten `web` så att ingenting skriver under återställningen (`db` kan vara uppe):
   ```bash
   docker compose stop web
   ```
2. Återställ till en ny eller tömd databas:
   ```bash
   docker compose exec -T db \
     pg_restore -U saldovibe -d saldovibe --clean --if-exists \
     < backups/db-<tidsstämpel>.dump
   ```
3. Starta `web` igen — entrypointen kör `migrate --noinput` automatiskt (se
   [upgrades-migrations.md](upgrades-migrations.md)), vilket inte gör något om den återställda
   dumpen redan ligger på aktuellt migrationsläge:
   ```bash
   docker compose start web
   ```

## Återställa media

```bash
docker run --rm \
  -v saldovibe_media-assets:/media \
  -v "$(pwd)/backups":/backup \
  alpine sh -c "rm -rf /media/* && tar xzf /backup/media-<tidsstämpel>.tar.gz -C /media"
```

## Verifiera att återställningen faktiskt fungerade

Efter återställning till en **staging**-kopia av stacken (verifiera aldrig destruktivt mot
produktion):

- Logga in och bekräfta att ett känt företag/en känd transaktion från före backupen finns.
- Öppna en känd bilaga/faktura-PDF och bekräfta att själva filen öppnas (inte bara DB-raden).
- Kör `python manage.py verify_audit_chain` mot den återställda databasen för att bekräfta att
  revisionsloggens hashkedja är intakt (se [logging-monitoring.md](logging-monitoring.md) och
  `docs/compliance/`).

## Rekommenderad rytm

- **Varje natt**: PostgreSQL-dump (automatiskt, se ovan).
- **Varje vecka**: mediabackup (automatiskt, se ovan).
- **Varje kvartal**: fullständigt återställningstest till staging, inte bara "backupfilen finns" —
  en otestad backup är ingen backup. Kombinera med den befintliga
  `docs/compliance/quarterly-review-checklist.md`.
