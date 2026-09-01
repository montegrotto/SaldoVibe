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

## 1. Årsavslut/bokslutsflöde

Det största funktionella hålet. Guidad avslutning av ett räkenskapsår:

- **Designdokument klart och alla öppna frågor beslutade (2026-08-16)** —
  `docs/compliance/aarsavslut/bokslutsflode-design.md`. Bolagsform blir ett nytt
  `TextChoices`-fält på `Company`; helårslåsningen körs *efter* bokslutsverifikationen (sista
  steget); år stängs i kronologisk ordning. Redo för implementation.
- Kontroll att perioderna fram till årets sista är låsta (`PeriodLock`) och att balansräkningen
  går ihop (`balance_difference == annual_result`, verifierad formel i designdokumentet).
- Två bokslutsverifikationer: S1 (8999 → 2099, årets sista dag) och S2 (2099 → 2091, nästa års
  första dag) för AB; enskild firma använder 2019/2010-serien på samma sätt. Detaljer i
  designdokumentet.
- Tydlig IB-överföring till nästa år. Obs: IB beräknas redan dynamiskt (`sie.py`,
  huvudboken) — det som saknas är själva omföringsverifikationen och guiden.
- Byggstenar: `AccountingYear`, `period_lock_lock_year`, huvudboken för verifiering.
  Gata bakom `finance_admin` i `compliance_policy.ACTION_ROLE_MATRIX`.

## 2. Periodiseringar

Boka en kostnad/intäkt över flera månader (försäkring, hyra) i ett svep:

- Formulär: belopp, konto, motkonto 17xx/29xx, antal månader, startmånad → skapar N
  verifikationer på en gång. Ingen bakgrundskörning — allt skapas direkt, precis som
  `recurring_invoice_generate`.
- Byggstenar: verifikationsmallarna (`verification_templates.py`) visar mönstret
  "generera verifikationer"; periodlåsen måste kontrolleras per verifikationsdatum.
- Enkel v1: ingen automatisk upplösning/reversering, bara skapandet.

## 3. Enkel resultatbudget

Belopp per konto och månad, som jämförelsekolumn i resultatrapporten:

- En modell (konto, år, månad, belopp) + ett redigeringsformulär per räkenskapsår.
- Extra kolumn (budget + differens) i `build_income_statement_context` och ev. på dashboarden.
- Inga prognosmotorer — likviditetsprognosen visar att ambitionsnivån "enkel och användbar"
  räcker.

## 4. Offerter

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
