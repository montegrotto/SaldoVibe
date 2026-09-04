# Funktionsplan

Kandidater från genomgången 2026-08-15, i prioritetsordning. Målet är oförändrat:
hålla systemet enkelt, utan direkta integrationer mot andra system. Varje punkt
ska återanvända befintliga byggstenar (se CLAUDE.md "Reuse Before Building") och
följa gällande grindar: lint + full testsvit + webbläsarverifiering, användarguiden
uppdateras i samma ändring.

## Klart

- ~~Huvudbok~~ — `bookkeeping:general_ledger`, commit `c762b26`.
- ~~Kund- och leverantörsreskontra med åldersanalys~~ — `bookkeeping:reskontra`, commit `c762b26`.
- ~~Betalningspåminnelser~~ — `invoicing:invoice_reminder_print`, commits `8bf3648` + `4cb85e1`
  (historik, numrering, avgift som företagsinställning, bekräftelsedialog).
- ~~Periodiseringar~~ — `bookkeeping:periodization_create`, commit `09b18bd`.
- ~~Enkel resultatbudget~~ — `bookkeeping:budget_edit`, commit `3d87f18`.
- ~~Årsavslut/bokslutsflöde~~ — `bookkeeping:year_end_close`, per
  `docs/compliance/aarsavslut/bokslutsflode-design.md`.

## Genomgång 2026-09-04

Punkterna 1–3 från förra genomgången är klara (se ovan). Ny inventering av vad som saknas, i
prioritetsordning.

### Små, hög nytta — byggs nu, en PR per punkt

- **Glömt lösenord** — `accounts` har bara login/logout/register; Djangos inbyggda
  `PasswordResetView` + befintlig utgående e-post.
- **Bjud in användare till företag + läsroll** — användare kopplas bara vid skapandet
  (`company_create`); ingen vy för att lägga till kollega/revisor. Rollmatrisen saknar en ren
  läsroll (revisorsåtkomst).
- **Global sök** — sökfält i sidhuvudet som slår på verifikationstext, belopp, fakturanummer
  och motpart.
- **Lönespec via e-post** — utskriften (`salary_report_print`) och utskicksinfrastrukturen
  (`outgoing_mail`) finns, ihopkopplingen saknas.

### Medelstora — reella hål i domänen

- **Semesterhantering i lön** — inga semesterdagar/semesterskuld i `payroll`. Semesterskulden
  ska bokföras vid bokslut, så bokslutsflödet är ofullständigt utan den.
- **SRU för enskild firma** — `Company` har bolagsformen men `sru_lookup` täcker bara INK2.
  NE-bilagan saknas.
- **Kontantmetoden (bokslutsmetoden) för moms** — bara faktureringsmetoden finns.
- **Tvåfaktorsinloggning** — TOTP går med stdlib `hmac`, inget nytt beroende.

### Stora — bara vid faktisk efterfrågan

- **Årsredovisning K2** — naturligt steg efter bokslutet, men mycket mall och regelverk.
- **Kostnadsställe/projekt** — ingen dimension i modellerna eller SIE-exporten (`#DIM`/`#OBJEKT`).
- **Valutahantering** — kundfakturan har valutafält men ingen kursdifferens vid betalning.

Attestflöde, lager och API: inga skäl i målgruppen, inte planerade.

## Offerter

Skapa offert från samma artiklar/kunder som fakturor, med "gör om till faktura"-knapp:

- Återanvänder nästan hela `invoicing` (rader, print-mall i fönsterkuvertsformat, kund/artikel).
- Bokför ingenting förrän den blivit faktura.
- **Bygg bara om användarna faktiskt skickar offerter** — annars YAGNI.

## Möjliga påbyggnader på det som redan finns

- ~~Tak/varning efter påminnelse 2 (inkassovarsel)~~ — varning på fakturans detaljvy när två
  påminnelser redan registrerats; medvetet ingen hård spärr.
- ~~Reskontra per valfritt datum~~ — datumväljare på `bookkeeping:reskontra`/PDF:en,
  ställning och avstämning beräknas per valt datum ur betalningshistoriken.
- ~~E-postutskick av fakturor/påminnelser~~ — tidigare medvetet bortvalt (SMTP-beroende),
  omprövat 2026-08: per-företag utgående konto (SMTP eller Microsoft Graph, återanvänder
  bilageimportens appregistrering) + globalt systemkonto för daglig notisdigest. PDF-flödet
  finns kvar som fallback; skickade mail loggas på fakturans detaljsida.

## Medvetet bortvalt
- **Bankkoppling/PSD2, BankID, e-fakturaväxel** — bryter mot den integrationsfria linjen;
  SIE/CSV-import täcker behovet. (Peppol-XML genereras redan som fil, utan växel.)
- **Kassaflödesanalys enligt K3 m.fl. rapportvarianter** — fel målgrupp för små K2-bolag.
