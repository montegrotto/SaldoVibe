# Registerförteckning — ROPA (G-002, art. 30)

Statusdatum: 2026-08-15

Registerförteckning över personuppgifter som behandlas i SaldoVibe. Roller enligt
`roles-and-scope.md`: vid självhostning är kundföretaget personuppgiftsansvarigt och denna
förteckning beskriver dess behandling; vid hostad drift behandlar operatören för den
personuppgiftsansvariges räkning. Bevarandetider definieras i `retention-schedule.md` och
refereras bara härifrån.

Ett automatiskt test (`auditlog`-testsviten) verifierar att varje modell i
`auditlog.services.TRACKED_MODELS` förekommer i modellmappningstabellen nedan, så en
nyspårad modell tvingar fram en ROPA-uppdatering.

## Aktiviteter

### A1 — Användarkonton och autentisering

- **Ändamål:** logga in applikationsanvändare, avgränsa dataåtkomst per företag.
- **Rättslig grund:** avtal (art. 6.1 b) — användarens relation till företaget.
- **Registrerade / kategorier:** applikationsanvändare — e-post, för-/efternamn,
  lösenordshash, inloggningstidsstämplar.
- **Mottagare/biträden:** inga (hostad drift: operatören).
- **Bevarande:** klass B, se `retention-schedule.md`.

### A2 — Lön

- **Ändamål:** beräkna och betala löner, rapportera AGI till Skatteverket.
- **Rättslig grund:** avtal (anställning) och rättslig förpliktelse (art. 6.1 c —
  skatteförfarandelagen/AGI, bokföringslagen för de resulterande verifikationerna).
- **Registrerade / kategorier:** anställda — namn, personnummer (krypterat i vila,
  `saldovibe/encryption.py`), adress, lön, skattetabell, lönehistorik.
- **Mottagare/biträden:** Skatteverket (AGI-XML, laddas upp manuellt av användaren;
  skattetabellslagningar via Skatteverkets API innehåller personnummer).
- **Bevarande:** klass A (räkenskapsinformation) för löneposter och AGI-evidens;
  klass B för inaktiva anställdas stamdata.

### A3 — Kundreskontra och fakturering

- **Ändamål:** ställa ut kundfakturor, följa betalning, Peppol-e-fakturering.
- **Rättslig grund:** avtal och rättslig förpliktelse (bokföringslagen).
- **Registrerade / kategorier:** kontaktpersoner / enskilda näringsidkare bland kunder —
  namn, org-/momsnummer, e-post, telefon, adress.
- **Mottagare/biträden:** Peppol-accesspunkt när e-fakturering används.
- **Bevarande:** klass A för fakturor; klass B för kundstamdata utan transaktioner.

### A4 — Leverantörsreskontra och utlägg

- **Ändamål:** registrera leverantörsfakturor och anställdas utläggskrav, betala dem.
- **Rättslig grund:** avtal och rättslig förpliktelse (bokföringslagen).
- **Registrerade / kategorier:** kontaktpersoner / enskilda näringsidkare bland
  leverantörer; anställda som lämnar utlägg — namn, bank-/betalningsuppgifter på fakturor,
  kvittoinnehåll.
- **Mottagare/biträden:** inga utöver A5 när kvitton bifogas.
- **Bevarande:** klass A för fakturor/utläggskrav; klass B för leverantörsstamdata utan
  transaktioner.

### A5 — Bilagor och OCR-extraktion

- **Ändamål:** lagra kvitto-/fakturabilder som räkenskapsinformation; extrahera fält via OCR.
- **Rättslig grund:** rättslig förpliktelse (bokföringslagen 7 kap. — verifikationer).
- **Registrerade / kategorier:** vad det uppladdade dokumentet råkar innehålla (namn,
  adresser, kortfragment på kvitton).
- **Mottagare/biträden:** inga — OCR-extraktionen körs in-process
  (`attachments/extraction_client.py`); bilagebytes lämnar aldrig SaldoVibe i detta steg.
- **Bevarande:** klass A.

### A6 — E-posthämtning av leverantörsfakturor

- **Ändamål:** hämta fakturabilagor automatiskt från en företagsbrevlåda.
- **Rättslig grund:** berättigat intresse (art. 6.1 f) — automatisering av företagets eget
  fakturaflöde från dess egen brevlåda.
- **Registrerade / kategorier:** e-postavsändare — adress, meddelandemetadata,
  bilageinnehåll; brevlådeuppgifter (app-lösenord / OAuth-hemlighet) för
  företagsbrevlådan.
- **Mottagare/biträden:** kundens egen e-postleverantör (Gmail / Microsoft 365) — kundens
  biträde, inte operatörens (se `roles-and-scope.md`).
- **Bevarande:** hämtade bilagor blir klass A via A5; inloggningsuppgifterna finns kvar
  tills företaget inaktiverar integrationen.

### A7 — Auditlogg

- **Ändamål:** oföränderlig behandlingshistorik över bokföringsdata (BFNAR 2013:2) och
  ansvarsskyldighet (art. 5.2).
- **Rättslig grund:** rättslig förpliktelse (bokföringslagen/BFNAR) och berättigat intresse
  (manipulationsspårbarhet).
- **Registrerade / kategorier:** före-/efterögonblicksbilder av alla modeller nedan, samt
  den agerande användarens identitet per post. Personnummer och brevlådeuppgifter maskeras
  före hashning (`sensitive_fields` i `auditlog/services.py`); namn/adresser maskeras inte.
- **Mottagare/biträden:** FreeTSA tar endast emot kedjans topphash (RFC
  3161-tidsstämpling) — inga personuppgifter.
- **Bevarande:** klass A; se `audit-chain-and-erasure.md` (G-007) för
  raderingsståndpunkten.

### A8 — Kärnbokföring

- **Ändamål:** dubbel bokföring, momsdeklarationer, SIE-export, årsredovisningar.
- **Rättslig grund:** rättslig förpliktelse (bokföringslagen).
- **Registrerade / kategorier:** personuppgifter endast i förbigående (verifikationstexter,
  motpartsnamn på transaktioner); företagets stamdata omfattar brevlådeuppgifterna som
  täcks av A6.
- **Mottagare/biträden:** Skatteverket (moms-/AGI-filer genereras lokalt, laddas upp av
  användaren); revisor/Skatteverket vid export.
- **Bevarande:** klass A.

### A9 — Backuper

- **Ändamål:** katastrofåterställning.
- **Rättslig grund:** följer den aktivitet vars data säkerhetskopieras.
- **Registrerade / kategorier:** fullständiga kopior av allt ovanstående.
- **Mottagare/biträden:** backuplagringsleverantör vid hostad drift.
- **Bevarande:** begränsat backupbevarande enligt G-014 (se `restore-runbook.md`);
  raderingar appliceras om efter varje återställning.

### A10 — Utgående e-post

- **Ändamål:** skicka kundfakturor och betalningspåminnelser som PDF till kundens
  e-postadress; skicka daglig notissammanfattning till företagets användare.
- **Rättslig grund:** berättigat intresse (art. 6.1 f) — fullgörande av företagets
  fakturering mot dess kunder; för notisdigesten fullgörande av tjänsten mot användaren.
- **Registrerade / kategorier:** kundens e-postadress och fakturainnehållet i utskicket;
  användarens e-postadress; sändloggen (`bookkeeping.SentEmail`: mottagare, ämne, status,
  felmeddelande). Företagets utgående SMTP-lösenord lagras krypterat på
  `bookkeeping.company` och maskeras i auditloggen (`sensitive_fields`).
- **Mottagare/biträden:** företagets egen e-postleverantör (SMTP-leverantör eller
  Microsoft 365 via Graph) — kundens biträde; systemets SMTP-leverantör för
  notisdigesten.
- **Bevarande:** sändloggen är klass B, se bevarandeschemat; utgående kontouppgifter
  finns kvar tills företaget tar bort konfigurationen.

## Modellmappning

Varje auditspårad modell (`auditlog.services.TRACKED_MODELS`) mappar till exakt en
aktivitet. Modeller markerade *inga direkta personuppgifter* ingår så att driftvakten täcker
hela registret.

| Modell | Aktivitet | Personuppgifter |
|---|---|---|
| accounts.customuser (ej auditspårad) | A1 | e-post, namn |
| bookkeeping.company | A8 / A6 / A10 | brevlådeuppgifter (krypterade i vila enligt G-009 — planerat), företagets kontaktuppgifter |
| bookkeeping.sentemail (ej auditspårad) | A10 | mottagarens e-postadress, ämnesrad |
| bookkeeping.account | A8 | inga |
| bookkeeping.accountingyear | A8 | inga |
| bookkeeping.transaction | A8 | i förbigående (verifikationstext) |
| bookkeeping.journalentry | A8 | i förbigående |
| bookkeeping.voucherseriesrule | A8 | inga |
| bookkeeping.periodlock | A8 | inga |
| bookkeeping.budgetline | A8 | inga |
| banking.bankaccount | A8 | inga |
| banking.bankimport | A8 | i förbigående (kontoutdragsrader) |
| banking.banktransaction | A8 | i förbigående (betalare-/mottagartext) |
| invoicing.customer | A3 | namn, e-post, telefon, adress |
| invoicing.article | A3 | inga |
| invoicing.invoice | A3 | motpartsuppgifter |
| invoicing.invoiceline | A3 | i förbigående |
| invoicing.invoicepayment | A3 | inga |
| invoicing.invoicereminder | A3 | inga |
| supplier_invoices.supplier | A4 | namn, e-post, telefon, adress |
| supplier_invoices.supplierinvoice | A4 | motpartsuppgifter |
| supplier_invoices.supplierinvoicecostline | A4 | i förbigående |
| supplier_invoices.supplierinvoicepayment | A4 | inga |
| expenses.expenseclaim | A4 | referens till den anställde, kvittodata |
| expenses.expenseclaimpayment | A4 | inga |
| payroll.employee | A2 | namn, personnummer (krypterat), adress, lönevillkor |
| payroll.payrollrun | A2 | inga direkt |
| payroll.salaryrecord | A2 | lönebelopp per anställd |
| payroll.salaryadjustment | A2 | justeringsbeskrivningar per anställd |
| payroll.payrollreportevidence | A2 | AGI-underlag (personnummer, lönebelopp) |
| vat.vatclosesnapshot | A8 | inga |
| attachments.transactionattachment | A5 | dokumentinnehåll |
| fixed_assets.fixedassettype | A8 | inga |
| fixed_assets.fixedasset | A8 | inga |
| fixed_assets.fixedassetdepreciation | A8 | inga |
| fixed_assets.fixedassetimpairment | A8 | inga |
| fixed_assets.fixedassetreclassification | A8 | inga |
| auditlog.auditlogentry (själva liggaren) | A7 | ögonblicksbilder + agerande användare |
