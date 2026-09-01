# SaldoVibe – Drift

Operativ dokumentation för att köra och underhålla SaldoVibe i produktion (Docker Compose-stacken:
`web` + PostgreSQL `db` + `nginx`). Grundläggande container-layout och miljöuppsättning finns redan
i root-`README.md` — det här är fördjupningen på det som saknades där.

Se [docs/user-guide/](../user-guide/README.md) för hur man **använder** appen, och
`docs/compliance/` för de bokförings-/revisionsspecifika kontrollerna (skilda från vanlig
infrastrukturdrift).

## Innehåll

1. [Miljövariabler](environment-variables.md) – konsoliderad referens för alla `os.getenv`-lästa värden
2. [Backup & restore](backup-restore.md) – PostgreSQL + media, separat från compliance-dry-run
3. [Deploy-checklista](deploy-checklist.md) – från `git pull` till levande release
4. [Uppgraderingar & migrationer](upgrades-migrations.md) – hur/när migrationer körs, rollback
5. [Loggning & övervakning](logging-monitoring.md) – var loggar hamnar, vad som är värt att bevaka
