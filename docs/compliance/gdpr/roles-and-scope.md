# GDPR-roller och omfattning (G-001)

Statusdatum: 2026-08-15

Detta dokument anger vem som är personuppgiftsansvarig och vem som är personuppgiftsbiträde för
personuppgifter som behandlas i SaldoVibe, per driftsmodell. Alla övriga dokument i
`docs/compliance/gdpr/` utgår från denna avgränsning. Det är teknisk vägledning, inte juridisk
rådgivning.

## Driftsmodeller

| Driftsmodell | Personuppgiftsansvarig | Personuppgiftsbiträde | SaldoVibes roll |
|---|---|---|---|
| Självhostad (kunden kör `docker-compose.yml` eller standalone-imagen på egen infrastruktur) | Kundföretaget, för sina anställda, kunder, leverantörer och applikationsanvändare | Ingen som SaldoVibe tillför | Endast mjukvaruleverantör — ingen åtkomst till personuppgifter, ingen GDPR-roll |
| Hostad drift (SaldoVibe/operatören driver instansen åt ett kundföretag) | Kundföretaget | Operatören (SaldoVibe-som-tjänst) | Biträde enligt art. 28 — ett personuppgiftsbiträdesavtal (PUB-avtal) krävs före driftstart |

Anmärkningar:

- De registrerade är i första hand kundföretagets **anställda** (lön), **kontaktpersoner hos
  kunder/leverantörer** (reskontror) och företagets egna **applikationsanvändare** (konton).
  Se `ropa.md` för hela inventeringen.
- Vid hostad drift måste operatörens underbiträden (hosting, backuplagring) listas i
  PUB-avtalet och i `processor-register.md` (G-011). OCR-fältextraktion för bilagor körs
  in-process, inte via ett separat underbiträde.
- Kundens egna e-postleverantörer (Gmail / Microsoft 365 för e-posthämtning) anlitas av
  kunden, under kundens egna avtal — de är kundens biträden, inte operatörens underbiträden.

## Disposition för PUB-avtal (hostad drift)

Ett PUB-avtal för hostad drift måste minst täcka (art. 28.3):

1. Föremål, varaktighet, art och ändamål: drift av bokföringstjänsten SaldoVibe åt
   kundföretaget.
2. Kategorier av registrerade och personuppgifter: enligt `ropa.md`.
3. Dokumenterade instruktioner: behandling endast för att driva tjänsten; ingen sekundär
   användning.
4. Konfidentialitetsåtagande för operatörens personal.
5. Säkerhetsåtgärder (art. 32): fältkryptering i vila för personnummer och
   e-postuppgifter (`saldovibe/encryption.py`), hashkedjad auditlogg, rollstyrda
   compliance-åtgärder (`bookkeeping/compliance_policy.py`), TLS under överföring.
6. Lista över underbiträden och mekanism för ändringsavisering.
7. Bistånd med de registrerades rättigheter (DSAR-runbook, G-005) och
   incidentrapportering (breach-response-runbook, G-010; operatören ska underrätta den
   personuppgiftsansvarige utan onödigt dröjsmål).
8. Radering eller återlämning av alla personuppgifter vid avtalets slut, förutsatt att
   operatören inte har någon egen lagstadgad bevarandeplikt (bokföringslagens
   arkiveringskrav binder *kunden*, inte operatören).
9. Revisions-/inspektionsrätt för den personuppgiftsansvarige.

## Konsekvenser för detta repo

- Funktioner och dokument måste fungera för **båda** modellerna: runbooks adresserar "den
  personuppgiftsansvarige" och "operatören" separat där skillnaden spelar roll.
- Inget i kodbasen får förutsätta en central SaldoVibe-part med åtkomst till kunddata;
  all dataåtkomst är företagsavgränsad (`bookkeeping/company_scope.py`).
