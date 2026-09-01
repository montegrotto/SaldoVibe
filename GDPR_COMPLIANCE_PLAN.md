# GDPR-efterlevnadsmatris och implementationsbacklogg

Statusdatum: 2026-08-14
Omfattning: bokföringsplattformen SaldoVibe (Django)

Detta dokument är en implementationsfokuserad efterlevnadsplan för
GDPR/dataskyddsförordningen, strukturerad som `SKATTEVERKET_COMPLIANCE_PLAN.md`. Det är
teknisk vägledning, inte juridisk rådgivning.

Personuppgifter som faktiskt behandlas idag (kartläggning av kodbasen, 2026-08-14):

- `accounts.CustomUser` — e-post, för-/efternamn för applikationsanvändare.
- `payroll.Employee` — namn, **personnummer** (rent `CharField`, `payroll/models.py:67`),
  adress, lön, skattetabell; flödar in i `SalaryRecord`-ögonblicksbilder och AGI-XML.
- `invoicing.Customer` / `supplier_invoices.Supplier` — namn, org-/momsnummer, e-post,
  telefon, adress (personuppgifter när motparten är en enskild firma eller en kontaktperson).
- `attachments` — uppladdade kvitton/fakturor innehåller de personuppgifter papperet råkar
  bära.
- `auditlog.AuditLogEntry` — oföränderliga före-/efterögonblicksbilder av allt ovanstående
  (personnummer och brevlådeuppgifter maskeras via `sensitive_fields` i
  `auditlog/services.py`; namn/adresser maskeras inte).
- `bookkeeping.Company.email_fetch_*` — brevlådeuppgifter (app-lösenord / OAuth-klienthemlighet)
  lagrade i klartext i vila (`bookkeeping/models.py:64,67`), endast maskerade i auditloggen.

Externa dataflöden idag: e-posthämtningen läser meddelanden från Gmail/Microsoft 365; FreeTSA
tar endast emot auditkedjans topphash (inga personuppgifter); AGI- och Peppol-filer genereras
lokalt och laddas upp av användaren själv — ingen automatiserad överföring. OCR-fältextraktion
för bilagor (`attachments/extraction_client.py`) körs in-process sedan 2026-08-18 (tidigare en
separat ReInvGrabber-tjänst) och är därför inte heller ett externt dataflöde.

## 1) Matris krav-till-implementation

Teckenförklaring:
- Status: Implemented / Partial / Missing
- Prioritet: P0 (kritisk), P1 (hög), P2 (medel)

| ID | Kravområde | Förväntat utfall | Aktuell implementationsevidens | Status | Gap | Rekommenderad implementationsändring | Testtäckning att lägga till | Acceptanskriterier | Prioritet |
|---|---|---|---|---|---|---|---|---|---|
| G-001 | Definierade roller ansvarig/biträde (art. 4, 24, 28) | Det är dokumenterat vem som är personuppgiftsansvarig och vem som är biträde per driftsmodell | `docs/compliance/gdpr/roles-and-scope.md`: roll per driftsmodell (självhostad = enbart mjukvara; hostad = biträde) + PUB-avtalsdisposition | Implemented | Varje annat krav i denna matris beror på denna avgränsning; en mall för personuppgiftsbiträdesavtal (PUB-avtal) behövs den dag SaldoVibe drivs åt någon annan | Skriv `docs/compliance/gdpr/roles-and-scope.md`: roll per driftsmodell, PUB-avtalsdisposition för hostad drift | ej tillämpligt — dokument | Rolluppdelningen är nedskriven och varje senare GDPR-dokument refererar den | P0 |
| G-002 | Registerförteckning (art. 30) | En registerförteckning listar varje behandlingsaktivitet med ändamål, rättslig grund, kategorier, mottagare, bevarande | `docs/compliance/gdpr/ropa.md`: aktiviteter A1–A9 med rättslig grund + modellmappning; driftvaktstest (`auditlog.tests.RopaDriftGuardTests`) verifierar att varje `TRACKED_MODELS`-post förekommer | Implemented | Personuppgiftskartläggningen överst i detta dokument är råmaterialet; den är bara inte strukturerad enligt art. 30 | Skriv `docs/compliance/gdpr/ropa.md`, ett avsnitt per aktivitet (användarkonton, lön, fakturering/kundreskontra, leverantörsreskontra, bilagor/OCR, e-posthämtning, auditlogg, backuper), vart och ett med ändamål, rättslig grund (avtal / rättslig förpliktelse via bokföringslagen / berättigat intresse), datakategorier, biträden, bevarande | ej tillämpligt — dokument; en CI-grep att varje modell i `auditlog.TRACKED_MODELS` med personuppgifter förekommer i ROPA:n är en billig driftvakt | Varje modell med personuppgifter mappar till exakt en ROPA-aktivitet med angiven rättslig grund och bevarande | P0 |
| G-003 | Bevarande- och raderingspolicy (art. 5.1 e, 17) | Personuppgifter har en definierad livstid; data utanför bokföringslagens 7-årsbevarande är raderingsbar | `docs/compliance/gdpr/retention-schedule.md`: klass A (räkenskapsinformation, 7 år) / klass B (ingen bevarandeplikt) per ROPA-aktivitet med triggrar. Klass B-radering implementerad (`gdpr_anonymize`, G-006); klass A-gallring designad (`retention-purge-design.md`, G-008), koden landar enligt dess ~2032-tidsplan | Implemented | Två klasser behöver separeras: (a) räkenskapsinformation — bevaras 7 år, därefter gallringsbar; (b) allt annat (inaktiv `Employee`-stamdata bortom lönehistoriken, `Customer`-/`Supplier`-poster utan transaktioner, vilande `CustomUser`-konton) — ingen lagstadgad bevarandeplikt alls | Skriv `docs/compliance/gdpr/retention-schedule.md` som mappar varje ROPA-aktivitet till en bevaranderegel + rättslig grund; implementera sedan radering för klass (b) (G-006, G-007) och en gallringsdesign efter bevarandetiden för klass (a) (G-008) | Bevarandeschemat granskat mot ROPA:n; senare: tester per raderingsväg | Varje ROPA-aktivitet har en bevarandetid och ett angivet skäl; inget "för evigt av misstag" | P0 |
| G-004 | Skydd av personnummer (art. 5.1 f, 32) | Personnummer skyddas i vila och visas aldrig i sin helhet där det inte krävs enligt lag | Krypterat i vila via `saldovibe/encryption.py` (Fernet, `SALDOVIBE_FIELD_ENCRYPTION_KEY`) med blint HMAC-index för unikhet; maskerat till `19900101-XXXX` i `employee_list.html`, `payroll_run_detail.html` och admin (`Employee.masked_personal_identity_number`); fullt värde endast i AGI-XML och lönebesked (`salary_report_print.html`); auditloggen maskerar både kolumn och index | Implemented | — | Maskera till `YYMMDD-XXXX`-stil (sista fyra) överallt utom i AGI-XML och lönebeskeds-PDF; kryptera kolumnen i vila (kryptering på applikationsnivå vid spara/läs — inget nytt tungt beroende om en liten Fernet-wrapper över den befintliga `cryptography`-installationen räcker); behåll den befintliga auditloggmaskeringen | Tester: anställdlista/admin/körningsdetalj renderar bara maskerat värde; AGI-XML och lönebesked bär fortfarande fullt värde; rundturstest av krypteringen | Ingen endpoint, mall eller adminsida returnerar fullt personnummer utom AGI-export och lönebesked; värdet är inte klartext i DB-filen | P0 |
| G-005 | Hantering av registrerades rättigheter (art. 15, 16, 17, 20) | Begäranden om tillgång/rättelse/radering/portabilitet kan besvaras inom en månad | `docs/compliance/gdpr/dsar-runbook.md` (inkl. avslagsmall enligt art. 17.3 b med bevarandetidens slutdatum); kommandot `dsar_export` samlar subjektets rad + varje FK/M2M-refererande rad + audithistorik som JSON (`bookkeeping/management/commands/dsar_export.py`, testat i `test_dsar_export.py`) | Implemented | — | Skriv `docs/compliance/gdpr/dsar-runbook.md` (inkludera avslagsvägen enligt art. 17.3 b för räkenskapsinformation med bevarandetidens slutdatum angivet i svaret); lägg till en per-person-export (ett management-kommando räcker till att börja med: givet en Employee/Customer/användare, samla deras rader + auditposter till JSON/PDF) | Test: DSAR-exportkommandot returnerar alla rader som refererar subjektet; avslagsmall finns | En DSAR kan besvaras enbart utifrån runbooken; raderingsbegäranden får antingen radering (G-006) eller ett motiverat lagstadgat avslag | P1 |
| G-006 | Radering/anonymisering av icke-bokföringspersonuppgifter (art. 17) | Data utan lagstadgad bevarandeplikt kan faktiskt raderas eller anonymiseras | Kommandot `gdpr_anonymize`: användare → `raderad-anvandare-<pk>@example.invalid` + oanvändbart lösenord + inaktiv; anställd/kund/leverantör → kontaktfält blankade; åtgärden läggs till i `<DATA_DIR>/gdpr-erasures.jsonl` för återapplicering efter återställning; `gdpr.erase` (finance_admin) i `compliance_policy.py`; tester täcker FK-integritet, kedjegiltighet efter radering, blockerad inloggning (`test_gdpr_anonymize.py`) | Implemented | — | Anonymisera snarare än radera där FK:er finns: ersätt användarens e-post/namn med `raderad-anvandare-<pk>@example.invalid`, blanka Employee-/Customer-kontaktfält när de är utanför bevarandetiden; styr som compliance-åtgärd i `bookkeeping/compliance_policy.py` (`gdpr.erase`), auditlogga själva åtgärden | Tester: anonymiseringen bevarar FK-integritet, `verify_audit_chain` passerar fortfarande efteråt, anonymiserad användare kan inte logga in | En raderingsbegäran för en användare eller en person utanför bevarandetiden kan verkställas i en behörighetsstyrd åtgärd, med huvudbok och auditkedja intakta | P1 |
| G-007 | Radering kontra den oföränderliga auditkedjan (art. 17 kontra BFL) | Spänningen mellan den append-only hashkedjan (R-001/R-004, DB-triggrar blockerar UPDATE/DELETE) och rätten till radering är löst by design, inte av en slump | Personnummer och inloggningsuppgifter maskeras *före* hashning (`sensitive_fields`), så de kommer aldrig in i kedjan — bra. Men namn/adresser i före-/efterögonblicksbilder gör det, och auditrader är oföränderliga på DB-nivå; ståndpunkten dokumenterad i `docs/compliance/gdpr/audit-chain-and-erasure.md`; `SensitiveFieldsGuardTests` fallerar om en spårad modell har ett personnummer-/lösenords-/hemlighetsfält utanför `sensitive_fields` | Implemented | — | Dokumentera i `docs/compliance/gdpr/audit-chain-and-erasure.md`: auditposter är räkenskapsinformation/behandlingshistorik enligt BFNAR 2013:2 och bär samma 7-åriga lagstadgade grund (art. 17.3 b); utöka `sensitive_fields` för varje nyspårad modell med högriskfält *före* första release, eftersom maskeringen bara gäller framåt | Test som verifierar att `sensitive_fields` täcker personnummerbärande fält på varje spårad modell (vakt i grep-stil) | Ett skriftligt, försvarbart svar finns på "radera mig ur auditloggen", och inget högriskfält kommer framöver in i kedjan omaskerat | P1 |
| G-008 | Gallring efter bevarandetiden (art. 5.1 e) | Räkenskapsinformation äldre än 7-årsfönstret kan förstöras | `docs/compliance/gdpr/retention-purge-design.md`: prefixgallring per räkenskapsår med gallringsmarkör i kedjan + omedelbar återförankring, `retention.purge` (system_admin), gallringsprotokoll, begränsningar för mellanliggande kod; implementation uppskjuten till ~2032 enligt designens egen tidsplan | Implemented (design; kod uppskjuten by design) | — | Designdokument först (`docs/compliance/gdpr/retention-purge-design.md`): gallring per räkenskapsår som tar bort transaktioner, bilagor och auditposter och återförankrar kedjan, körbar först när `end_date` + 7 år har passerat, styrd till `system_admin` | Uppskjuten tillsammans med implementationen | En skriftlig design finns; implementation schemalagd innan den första driftsättningens data passerar gränsen (~2032) | P2 |
| G-009 | Lagring av inloggningsuppgifter (art. 32) | Brevlådeuppgifter ligger inte i klartext i vila | Båda fälten är `EncryptedTextField` (`saldovibe/encryption.py`), migrationen krypterar befintliga rader; auditloggen maskerar dem; rå-DB och rundtur testade (`test_credential_encryption.py`) | Implemented | — | Kryptera i vila med samma lilla wrapper som G-004 (en delad hjälpare, två anropsplatser) | Rundturstest; test att råvärdet i DB inte är klartexten | Uppgifterna oläsbara i en kopierad DB-fil; e-posthämtningen fungerar fortfarande | P1 |
| G-010 | Incidenthantering (art. 33, 34) | En incident bedöms och anmäls vid behov till IMY inom 72 h | `docs/compliance/gdpr/breach-response-runbook.md` (72-timmarsklocka, IMY-beslutsträd, uppdelning för hostad drift, evidenslogg) + GDPR-övningsavsnitt i `quarterly-review-checklist.md` | Implemented | — | Skriv `docs/compliance/gdpr/breach-response-runbook.md`: upptäcktskällor (röda indikatorer på compliance-dashboarden, R-014), bedömning, IMY-anmälningsbeslutsträd, 72-timmarsklocka, tröskel för underrättelse av registrerade, evidenslogg; lägg till en rad i `quarterly-review-checklist.md` för att gå igenom den | ej tillämpligt — dokument + kvartalsövning | En jourperson kan följa runbooken utan att behöva hitta på process under en incident | P1 |
| G-011 | Biträdesinventering och överföringar (art. 28, 44) | Varje tredje part som rör personuppgifter listas med sin roll och överföringsgrund | `docs/compliance/gdpr/processor-register.md`: e-postleverantörer (kundens eget biträde — uttalat), FreeTSA-bedömning om ej personuppgift, Skatteverket, Peppol, hosting | Implemented | Inget — den öppna punkten om ReInvGrabbers hostingplats är löst: extraktionen körs nu in-process (2026-08-18), så det finns inget externt biträde att bekräfta hosting för | Skriv `docs/compliance/gdpr/processor-register.md`; e-posthämtningsleverantörerna är kundens egen brevlåda (kundens biträde, inte SaldoVibes) — säg det uttryckligen | ej tillämpligt — dokument | Varje utgående dataflöde i kodbasen förekommer i registret med roll + överföringsgrund | P1 |
| G-012 | Integritetspolicy och kakor (art. 13, 14; ePrivacy) | Användarna informeras; kakanvändningen är inventerad | `docs/user-guide/12-integritetspolicy.md` (renderas på `/hjalp/`): inventering av användardata, kaktabell (`sessionid` + `csrftoken`, endast förstapartskakor), motivering till att banner saknas, rättigheter inkl. auditloggens art. 17.3 b-förbehåll; täcks av de generiska renderingstesterna för hjälpkapitel | Implemented | — | Lägg till ett statiskt integritetspolicykapitel i `docs/user-guide/` (renderas gratis på `/hjalp/` via `bookkeeping/help_docs.py`) som täcker vad SaldoVibe lagrar om *användaren*, plus kakinventeringen och motiveringen till att banner saknas | Mallrenderingstest av hjälpkapitlet | Policyn är nåbar från appen; kakinventeringen dokumenterad | P2 |
| G-013 | DPIA-screening (art. 35) | Integritetskänsliga funktioner får en skriftlig screening före lansering | `docs/compliance/gdpr/dpia-screening-template.md`: mall + retroaktiva screeningar för lön, e-posthämtning och OCR (alla: ingen fullständig DPIA, med angivna omscreeningstriggrar) | Implemented | — | Lägg till `docs/compliance/gdpr/dpia-screening-template.md` (en sida: funktion, data, risk, varför en fullständig DPIA utlöses/inte utlöses); dokumentera de tre befintliga funktionerna retroaktivt | ej tillämpligt — dokument | Nya integritetskänsliga funktioner levereras med genomförd screening; de tre befintliga är retroaktivt dokumenterade | P2 |
| G-014 | Backuper kontra radering (art. 17, 32) | Backupbevarandet är begränsat så att raderad data åldras ut; återställningar återuppväcker inte raderade subjekt | `restore-runbook.md` § "Backupbevarande och GDPR-omradering": rullande 90-dagarsgräns; återapplicera `gdpr-erasures.jsonl`-poster efter varje skarp återställning | Implemented | — | Lägg till ett backupbevarandeavsnitt i `docs/compliance/restore-runbook.md` (t.ex. rullande 90 dagar) och ett runbook-steg: kör om väntande raderingar efter varje återställning | Utöka checklistan för restore-dry-runnen | Backupbevarandet har en angiven gräns; restore-runbooken innehåller omraderingssteget | P2 |

## 2) Fasindelad backlogg (utförandeordning)

## Fas 0: Grund och röda flaggor — KLAR 2026-08-15
Mål: åtgärda den enda konkreta exponeringen och skriv dokumenten allt annat hänger på.

1. ✅ Personnummermaskering + kryptering i vila (G-004).
- Maskerat i `employee_list.html`, `payroll_run_detail.html`, `payroll/admin.py`.
  (`salary_report_print.html` visade sig *vara* lönebeskedet och behåller fullt värde,
  enligt acceptanskriterierna.)
- Kolumnen krypterad via `saldovibe/encryption.py`; återanvänds senare för G-009.

2. ✅ Beslutsdokument om roller och omfattning (G-001).

3. ✅ ROPA (G-002) med driftvakt, samt bevarandeschema (G-003:s dokumenthalva;
   raderingsmekaniken ligger kvar i fas 1).

Definition av klar för fas 0 (uppfylld):
- Ingen sida eller adminvy visar fullt personnummer utanför AGI/lönebesked; kolumnen krypterad i vila.
- `docs/compliance/gdpr/` finns med roles-and-scope, ropa, retention-schedule.

## Fas 1: Rättigheter och runbooks — KLAR 2026-08-15
Mål: kunna faktiskt svara en registrerad och överleva en incident.

1. ✅ DSAR-runbook + per-person-exportkommando (G-005) — `dsar_export`.
2. ✅ Anonymisering av användarkonton + radering av stamdata utanför bevarandetiden (G-006) —
   `gdpr_anonymize`, `gdpr.erase` i rollmatrisen, raderingslogg i
   `<DATA_DIR>/gdpr-erasures.jsonl`.
3. ✅ Ståndpunktsdokument auditkedja-och-radering + täckningsvakt för `sensitive_fields` (G-007).
4. ✅ Incidentrunbook + kvartalsvis GDPR-avsnitt (G-010).
5. ✅ Biträdesregister (G-011) — den öppna ReInvGrabber-punkten löst 2026-08-18 (extraktionen
   nu in-process, se nedan).

Definition av klar för fas 1 (uppfylld):
- En DSAR (tillgång eller radering) är besvarbar från början till slut utifrån runbookerna.
- Anonymiseringen passerar FK-integritets- och `verify_audit_chain`-tester.

## Fas 2: Härdning och information — KLAR 2026-08-15
Mål: stänga de återstående art. 32- och transparenspunkterna.

1. ✅ Kryptera brevlådeuppgifter i vila (G-009) — återanvände fas 0-hjälparen.
2. ✅ Integritetspolicykapitel `docs/user-guide/12-integritetspolicy.md` + kakinventering (G-012).
3. ✅ DPIA-screeningmall + retroaktiva screeningar för lön/e-posthämtning/OCR (G-013).
4. ✅ Backupbevarandegräns (rullande 90 dagar) + omraderingssteg vid återställning (G-014).

## Fas 3: Lång horisont — design KLAR 2026-08-15, gallringskod uppskjuten till ~2032
1. ✅ Designdokument för gallring efter bevarandetiden (G-008) — `retention-purge-design.md`;
   implementation schemalagd innan den första driftsättningens äldsta räkenskapsår passerar
   gränsen.
2. ✅ GDPR-rader i `docs/compliance/quarterly-review-checklist.md` (gjort i fas 1:
   incidentövning, DSAR-logg, biträdesregister, ROPA-drift).

Öppen punkt löst 2026-08-18: OCR-fältextraktionen flyttad in-process (beroendet
`reinvgrabber-extraction`, se `attachments/extraction_client.py`), vilket helt tog bort
ReInvGrabber som separat biträde — ingen hostingplats att bekräfta.

## 3) Föreslagen arbetsnedbrytning (initiala ärenden)

### Epic F: Skydd av personnummer och inloggningsuppgifter
- F1: Delad fältkrypteringshjälpare (Fernet över befintlig `cryptography`).
- F2: Kryptera + maskera `Employee.personal_identity_number`; migration för befintliga rader.
- F3: Kryptera `email_fetch_password` / `email_fetch_oauth_client_secret`.
- F4: Regressionstester: maskerad rendering överallt, fullt värde endast i AGI-XML + lönebesked, ingen klartext i DB.

### Epic G: GDPR-dokumentationssvit (`docs/compliance/gdpr/`)
- G1: roles-and-scope.md (+ PUB-avtalsdisposition).
- G2: ropa.md + CI-driftvakt mot `TRACKED_MODELS`.
- G3: retention-schedule.md.
- G4: dsar-runbook.md, breach-response-runbook.md, processor-register.md.
- G5: audit-chain-and-erasure.md, dpia-screening-template.md (+ tre retroaktiva screeningar).

### Epic H: Raderingsmekanik
- H1: Åtgärden `gdpr.erase` i `compliance_policy.py`-rollmatrisen.
- H2: Kommando/vy för anonymisering av användarkonton.
- H3: Anonymisering av stamdata utanför bevarandetiden (kontaktfält för Employee/Customer/Supplier).
- H4: Per-person-exportkommando för DSAR.
- H5: Tester: FK-integritet, auditkedjan verifierar efter radering, anonymiserad inloggning blockerad.

### Epic I: Operativa avslut
- I1: Backupbevarandegräns + omraderingssteg i restore-runbooken.
- I2: Integritetspolicykapitel i användarhandboken.
- I3: GDPR-rader i kvartalschecklistan.
- I4: Designdokument för gallring efter bevarandetiden.

## 4) QA-strategi för GDPR-kritiska vägar

1. Maskeringstester — rendera varje sida/admin/export som rör personnummer; verifiera att fullt värde bara förekommer i AGI-XML och lönebeskeds-PDF.
2. Krypteringstester — råvärdet i DB ≠ klartext; rundtur genom modellen fungerar; migration av befintlig data verifierad.
3. Raderingstester — anonymisera en användare/person, sedan: FK-integriteten håller, `verify_audit_chain` passerar, inloggning blockeras, ingen mall renderar det gamla namnet.
4. DSAR-exporttester — varje tabell som refererar subjektet förekommer i exporten.
5. Maskeringsvakt — automatisk kontroll att personnummerliknande fält på spårade modeller finns i `sensitive_fields` innan de kan komma in i hashkedjan.

## 5) Föreslagna repo-artefakter att lägga till härnäst

1. `docs/compliance/gdpr/roles-and-scope.md`
2. `docs/compliance/gdpr/ropa.md`
3. `docs/compliance/gdpr/retention-schedule.md`
4. `docs/compliance/gdpr/dsar-runbook.md`
5. `docs/compliance/gdpr/breach-response-runbook.md`
6. `docs/compliance/gdpr/processor-register.md`
7. `docs/compliance/gdpr/audit-chain-and-erasure.md`
8. `docs/compliance/gdpr/dpia-screening-template.md`

Strukturell referens: `erp-mafia/accounted` (AGPL, samma svenska bokföringsdomän) har en mogen
`.compliance/`-svit (ropa.yaml, dsar_runbook.md, DPIA-screeningar, dataklassificering inkl.
mönstret sista-fyra/kryptering-i-vila för personnummer) värd att läsa för strukturen innan vi
skriver vår egen.

## 6) Rekommendation för första sprinten

Implementera fas 0 i denna ordning:
1. Maskera personnummer i de tre mallarna och Django admin (ren visningsändring, levereras samma dag).
2. Kryptera personnummerkolumnen i vila, med migration och tester.
3. Skriv roles-and-scope.md — ensidesavgränsningen allt annat hänvisar till.
4. Skriv ropa.md och retention-schedule.md utifrån kartläggningen i detta dokument.

Denna ordning tar bort det enda skarpa dataexponeringsfyndet först och låser upp varje senare dokument.
