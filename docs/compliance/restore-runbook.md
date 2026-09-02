# Runbook för återställning

## Syfte

Validera att SaldoVibes bokföringsdata kan återställas och läsas konsistent.

## Omfattning

- Operativ SQLite-databas (lokalt/dev) eller PostgreSQL (produktion)
- Centrala bokförings- och auditloggstabeller
- Integritet i auditloggens hashkedja

## Automatiskt schema (produktion)

`docker-compose.yml` kör en dedikerad `scheduler`-tjänst (Ofelia) som exekverar
`/usr/local/bin/monthly-restore-dry-run.sh` inuti den körande `web`-containern kl. 03:00 den
1:a varje månad (`ofelia.job-exec.monthly-restore-dry-run.*`-labels på `web`-tjänsten).
Evidens skrivs till `/data/compliance-evidence/restore-tests`, som ligger på den persisterade
volymen `saldovibe-data` och därmed överlever att containern återskapas/deployas om. Inget
manuellt steg krävs i normaldrift; proceduren nedan är för lokal verifiering eller om det
schemalagda jobbet behöver köras om manuellt.

Bekräfta att schemat är registrerat: `docker compose logs scheduler`.
Trigga på begäran: `docker compose exec web /usr/local/bin/monthly-restore-dry-run.sh`.

## Manuell/lokal procedur

1. Aktivera projektmiljön.
2. Kör `scripts/monthly_restore_dry_run.sh`.
3. Bekräfta att skriptet avslutas med statuskod `0`.
4. Spara genererade evidensartefakter under `docs/compliance/evidence/restore-tests/` (lokalt/dev,
   SQLite) eller `/data/compliance-evidence/restore-tests` (produktion, PostgreSQL).
5. Granska rapportinnehållet och kontrollera att tabellantalen är rimliga.

## Kommandoreferens

- `./scripts/monthly_restore_dry_run.sh`
- Direktkommando (SQLite/dev): `./.venv/bin/python manage.py compliance_restore_dry_run --output-dir docs/compliance/evidence/restore-tests`
- Direktkommando (PostgreSQL/prod): `python manage.py compliance_restore_dry_run --output-dir /data/compliance-evidence/restore-tests`

## Förväntade artefakter

- SQLite: `restore-copy-<timestamp>.sqlite3`
- PostgreSQL: `restore-dump-<timestamp>.dump` (`pg_dump` i custom-format, återställningsbar med `pg_restore`)
- `restore-report-<timestamp>.json` (båda motorerna)
- Lyckad hashkedjeverifiering i operatörsloggarna

## Extern förankring av hashkedjan (RFC 3161)

`reseal_audit_chain --apply` kan räkna om prev_hash/entry_hash för kedjorna (en hashkedja per
företag, plus det frysta globala legacy-segmentet med `hash_version=1`), vilket får en
internt manipulerad kedja att åter se självkonsistent ut för enbart `verify_audit_chain`.
Månadsjobbet kör därför även `verify_audit_chain_anchors` (kontrollerar varje tidigare lagrat
ankares kryptografiska signatur och att den förankrade postens hash fortfarande matchar det som
intygades) och därefter `anchor_audit_chain` (skickar hashen för varje kedjas aktuella topp till en
extern RFC 3161-tidsstämplingstjänst — standard `https://freetsa.org/tsr`, konfigurerbar via
`AUDIT_CHAIN_TSA_URL` — och lagrar det signerade svaret som en `AuditChainAnchor`-rad). En
avvikelse i `verify_audit_chain_anchors` bevisar manipulation även när `verify_audit_chain`
rapporterar kedjan som giltig, eftersom en omförsegling inte retroaktivt kan ändra vad en
oberoende tredje part redan har intygat.

Verifieringen använder `openssl ts -verify` (inte `rfc3161ng`:s egen kontroll, som bara stödjer
RSA-signerade tokens och kastar fel på FreeTSA:s ECDSA-signerade svar) mot det medföljande
CA-certifikatet i `auditlog/data/freetsa_cacert.pem`. Ett TSA-avbrott innebär bara ett längre
fönster av ointygad historik fram till nästa lyckade körning — det försvagar inte något redan
registrerat ankare, så månadsskriptet behandlar det som en varning, inte ett hårt fel.

Förankra eller verifiera manuellt: `python manage.py anchor_audit_chain` /
`python manage.py verify_audit_chain_anchors`.

## Avstämning logg mot databas

`verify_audit_chain` bevisar bara att auditloggens egna poster hänger ihop — inte att de levande
raderna fortfarande matchar loggen. En rå `DELETE` direkt mot databasen kringgår ORM-signalerna
som annars skulle logga raderingen, så själva hashkedjan förblir giltig medan raden är borta.
Månadsjobbet kör därför även `reconcile_audit_log`, som för varje spårad modell tar fram de
objekt loggen registrerat som skapade men aldrig raderade och kontrollerar att de fortfarande
finns i databasen. En saknad rad avslutar med statuskod `1` (hårt fel) och pekar ut modell och
objekt-ID. Objekt som skapades innan auditloggen fanns saknar CREATE-post och kan inte kontrolleras.

Stäm av manuellt: `python manage.py reconcile_audit_log`.

## PostgreSQL-återställningens mekanik

Dry-runnen rör aldrig den skarpa databasen. Den dumpar den skarpa databasen med `pg_dump`,
återställer dumpen i en engångsdatabas (`<db>_restore_dryrun_<timestamp>`), verifierar radantal
där och släpper därefter alltid engångsdatabasen (lyckat eller ej). `pg_dump`/`pg_restore` i
runtime-imagen är pinnade till PostgreSQL 17-klientpaket för att matcha
`postgres:17-alpine`-servern; en klient nyare än servern misslyckas vid återställning med felet
`unrecognized configuration parameter` (en v17-klient mot en v16-server föll så här på
`transaction_timeout`), så bumpa klienten och server-imagen tillsammans.

## Backupbevarande och GDPR-omradering (G-014)

- **Backupbevarandet är begränsat: rullande 90 dagar.** Äldre backuper raderas så att
  anonymiserade/raderade personuppgifter (GDPR art. 17) åldras ut ur backupmängden i stället
  för att finnas kvar på obestämd tid. En driftsättning som behåller backuper längre måste
  dokumentera varför och spegla det i `gdpr/retention-schedule.md` (aktivitet A9).
- **Efter varje skarp återställning** (inte dry-runnen): applicera om raderingar som utförts
  efter att backupen togs. Varje anonymisering loggas i `<DATA_DIR>/gdpr-erasures.jsonl` —
  håll denna fil utanför databasbackupcykeln (den överlever återställningen) och kör sedan om
  `gdpr_anonymize` för varje post nyare än backupens tidsstämpel. Se
  `gdpr/dsar-runbook.md` § 3.

## Eskalering

Om restore-dry-run, `verify_audit_chain` eller `verify_audit_chain_anchors` misslyckas:

1. Frys nya bokföringsimporter/-exporter.
2. Spara utdata från det misslyckade kommandot.
3. Meddela teknikansvarig och ekonomiansvarig.
4. Öppna ett incidentärende och bifoga senaste lyckade evidensartefakt.
