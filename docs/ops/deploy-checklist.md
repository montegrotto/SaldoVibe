---
description: "Steg-för-steg-checklista för att driftsätta en ny SaldoVibe-release i Docker Compose-stacken."
---

# Deploy-checklista (produktion)

Steg för steg för att ta en ny release av `main` i drift på `docker-compose.yml`-stacken
(`web` + PostgreSQL `db` + `nginx`).

## Första installationen

1. Kopiera env-mallen och fyll i riktiga värden:
   ```bash
   cp .env.example .env
   ```
   Bara `docker-compose.yml` och `.env` behövs på servern — ingen utcheckning. Hämta dem från
   `main` (matchar `latest`-imagen; byt till en `vX.Y.Z`-tagg för att låsa en release):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/montegrotto/SaldoVibe/main/docker-compose.yml
   curl -fsSL -o .env https://raw.githubusercontent.com/montegrotto/SaldoVibe/main/.env.example
   ```
   Avkommentera hela avsnittet "Produktion" i filen. Ändra minst `DJANGO_SECRET_KEY` och
   `DATABASE_PASSWORD` / `POSTGRES_PASSWORD` från `change-me`, och sätt `SALDOVIBE_PUBLIC_URL`
   till den riktiga publika adressen (den styr `ALLOWED_HOSTS` och `CSRF_TRUSTED_ORIGINS` — se
   [environment-variables.md](environment-variables.md)).
2. **TLS termineras inte av den medföljande `nginx`-tjänsten** — nginx-konfigurationen som ligger
   inline i `docker-compose.yml` lyssnar bara på port 80. Sätt en TLS-terminerande reverse proxy
   eller lastbalanserare framför (eller lägg till ett TLS-serverblock) innan stacken exponeras
   publikt.
3. Starta stacken, antingen med den publicerade imagen från Docker Hub eller ett lokalt bygge:
   ```bash
   docker compose pull && docker compose up -d
   docker compose up --build -d   # bygg från den här utcheckningen i stället
   ```
   Lås en release med `SALDOVIBE_VERSION=1.2.3` i rotens `.env` (se `.env.example`).
   Containern `web` kör `manage.py migrate --noinput` automatiskt vid start (se
   [upgrades-migrations.md](upgrades-migrations.md)), så schemat skapas vid första uppstarten.
4. Skapa en admin/första användare via appens eget registreringsflöde
   ([användarhandboken, kapitel 1](../user-guide/01-komma-igang.md)) — inget separat steg för att
   skapa en Django-superuser behövs för normal användning.

## Rutinrelease (koden redan mergad till `main`)

1. **Ta backup först.** Ta en PostgreSQL-dump före deployen — se
   [backup-restore.md](backup-restore.md). En dålig migration är mycket billigare att återhämta
   sig från med en färsk dump än utan.
2. Hämta den nya koden på servern:
   ```bash
   git pull origin main
   ```
3. Hämta (eller bygg om) och starta om `web`-imagen — `migrate` körs vid containerstart:
   ```bash
   docker compose pull web && docker compose up -d web
   docker compose up --build -d web   # lokalt bygge i stället
   ```
4. Följ `web`-loggarna under uppstarten efter migrationsfel eller krashloopar:
   ```bash
   docker compose logs -f web
   ```
5. Röktesta releasen:
   - Logga in.
   - Öppna översikten för ett befintligt företag.
   - Skapa (eller visa) en verifikation för att bekräfta att DB-skrivningar fungerar.
   - Kontrollera att `/static/`-filer laddas (ingen saknad CSS/JS — ett tecken på att
     `collectstatic` inte kördes).
6. Om något är riktigt fel: rulla tillbaka till föregående image/tagg och återställ backupen från
   före deployen om migrationen redan hunnit köra destruktivt (se
   [upgrades-migrations.md](upgrades-migrations.md) för fallgropar vid rollback).

## Om driftavbrott

Stacken stödjer för närvarande **inte** rullande deploy utan avbrott — `docker compose up
--build -d web` återskapar den enda `web`-containern, vilket ger ett kort avbrott medan den
startar om. För ett bokföringsprogram för ett företag/liten skala är det oftast acceptabelt; om
det slutar vara det är det en signal att införa flera `web`-repliker bakom `nginx` med en
hälsokontroll innan trafiken flyttas över.

## Efter deploy

- Bekräfta att de schemalagda jobben fortfarande körs: ofelias backupjobb och
  återställnings-torrkörningen från `docs/compliance/` (`docker compose logs scheduler`, se
  [backup-restore.md](backup-restore.md) och `docs/compliance/restore-runbook.md`), plus hostens
  off-host-backupsynk, särskilt om något i `.env` ändrades.
- Uppdatera uppföljningen i `docs/compliance/quarterly-review-checklist.md` om releasen rörde
  något på den checklistan (periodlåsning, exporter, revisionslogg).
