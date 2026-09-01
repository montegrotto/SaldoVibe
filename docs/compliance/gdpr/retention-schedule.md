# Bevarandeschema (G-003, art. 5.1 e och 17)

Statusdatum: 2026-08-15

Bevaranderegler per ROPA-aktivitet (`ropa.md`). Två klasser:

- **Klass A — räkenskapsinformation.** Bokföringslagen 7 kap. 2 §: bevaras till och med slutet
  av det sjunde året efter det kalenderår då räkenskapsåret avslutades. Under det fönstret
  avslås raderingsbegäranden enligt art. 17.3 b (se DSAR-runbooken, G-005); därefter är
  uppgifterna gallringsbara (gallringsdesign: G-008, `retention-purge-design.md`).
- **Klass B — ingen lagstadgad bevarandeplikt.** Kan raderas/anonymiseras på begäran eller när
  den angivna triggern inträffar (mekanik: G-006).

| ROPA-aktivitet | Data | Klass | Bevaranderegel | Grund |
|---|---|---|---|---|
| A1 användarkonton | CustomUser-rader | B | Anonymisera när användaren lämnar företaget och inte har några öppna åtaganden; granska vilande konton årligen | Art. 5.1 e |
| A2 lön | SalaryRecord, PayrollRun, AGI-evidens | A | 7 år efter räkenskapsårets slut | BFL 7:2; SFL för AGI-underlag |
| A2 lön | Anställdas stamdata (inaktiva) | B | Blanka kontaktfält när personen inte förekommer i någon icke-utgången klass A-post; behåll namn + maskerat pnr så länge referenser finns | FK-integritet + art. 5.1 e |
| A3 kundreskontra | Fakturor och betalningar | A | 7 år efter räkenskapsårets slut | BFL 7:2 |
| A3 kundreskontra | Kundstamdata utan transaktioner | B | Radera på begäran eller efter 2 år utan aktivitet | Art. 5.1 e |
| A4 leverantörsreskontra / utlägg | Leverantörsfakturor, utläggskrav, betalningar | A | 7 år efter räkenskapsårets slut | BFL 7:2 |
| A4 leverantörsreskontra | Leverantörsstamdata utan transaktioner | B | Radera på begäran eller efter 2 år utan aktivitet | Art. 5.1 e |
| A5 bilagor | Uppladdade verifikationer | A | 7 år efter räkenskapsårets slut | BFL 7:2 (verifikationer) |
| A6 e-posthämtning | E-postuppgifter (inloggning) | B | Radera när integrationen inaktiveras | Art. 5.1 e, 32 |
| A6 e-posthämtning | Hämtade bilagor | A | Blir verifikationer via A5 | BFL 7:2 |
| A7 auditlogg | AuditLogEntry-kedjan | A | 7 år efter det räkenskapsår som dess nyaste refererade post tillhör; ståndpunktsdokument G-007 | BFNAR 2013:2, art. 17.3 b |
| A8 kärnbokföring | Transaktioner, journalposter, moms-snapshots, SIE | A | 7 år efter räkenskapsårets slut | BFL 7:2 |
| A9 backuper | Fullständiga DB-/mediakopior | — | Rullande gräns enligt `restore-runbook.md` (G-014); raderingar appliceras om efter återställning | Art. 17, 32 |
| A10 utgående e-post | SentEmail-sändlogg (mottagaradress, ämne, status) | B | Radera rader äldre än 2 år (driftlogg, inte räkenskapsinformation); följer företaget vid borttagning (CASCADE) | Art. 5.1 e |
| A10 utgående e-post | Utgående e-postuppgifter på Company (SMTP-lösenord för utgående resp. notiskonto) | B | Radera när företaget tar bort konfigurationen | Art. 5.1 e, 32 |

Tumregler:

- Inget bevaras "för evigt av misstag": varje tabell ovan har antingen en lagstadgad klocka
  (klass A) eller en trigger (klass B).
- Klass B-radering är **anonymisering, inte radborttagning**, överallt där främmande nycklar
  eller auditkedjan refererar raden (G-006).
- 7-årsklockan löper från slutet av det **kalenderår** då räkenskapsåret avslutades, inte från
  postens eget datum.
