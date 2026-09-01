# Årsavslut/bokslutsflöde – designdokument (Funktionsplan #1)

Status date: 2026-08-16
Status: **design godkänd — inte implementerad.** Detta dokument låser de beslut som krävs innan
kod, migrationer eller tester skrivs (se `docs/feature-roadmap.md` #1: "kräver ett designdokument
först, i stil med gdpr-gallringens", commit `e110c4a`). De tidigare öppna frågorna är nu
avgjorda (2026-08-16): kodfakta (teckenformel, 8999-beteende, ledig seriebokstav) är verifierade
mot koden, och beslutspunkterna (bolagsform, låsordning, enskild firma-konton, 8999-visning) är
beslutade av Mattias – se Beslutslogg.

## Vad flödet ska göra

En guidad, stegvis avslutning av **ett räkenskapsår** (`AccountingYear`) för det aktiva företaget:

1. Kontrollera att året är klart för avslut (tidigare år stängda, perioderna fram till årets
   sista låsta, balansräkningen stämmer med förväntad differens).
2. Skapa **bokslutsverifikationerna** – S1 (årets sista dag, stängda året) flyttar årets
   resultat till 2099/2019, S2 (första dagen, nästa år) för om det till balanserat resultat
   (2091) respektive konsoliderar 2010-serien. Se "Två verifikationer" under Steg 2.
3. Bekräfta/visa **IB-överföringen** till nästa räkenskapsår (skapas inline i wizarden om det
   saknas).
4. Helårslåsning av året via befintlig `period_lock_lock_year` – **sista** steget, efter att
   bokslutsverifikationerna är bokförda (se låsordningen under Steg 1).

Flödet skapar inga nya beräkningsmodeller – det paketerar befintliga byggstenar
(`bookkeeping/period_locking.py`, `bookkeeping/reports.py`, `bookkeeping/models.py`s
`Transaction`/`JournalEntry`) i en guide, plus **en ny sorts verifikation**
(bokslutsverifikationen) som idag inte kan skapas i systemet.

## Steg 1: Kontroll innan avslut

### Periodlås – verifikation först, helårslåsning sist (beslutat)

Tidigare version av detta dokument krävde att **hela** året var låst (`year_lock_status ==
"locked"`) innan bokslutsverifikationen skapades. Det motsäger sig självt:
`bookkeeping/views/transactions.py` (rad ~151) blockerar via `is_date_locked` varje ny
verifikation daterad i ett låst intervall, och bokslutsverifikationen dateras på årets sista dag.
Ett fullt låst år gör alltså bokslutsverifikationen omöjlig att bokföra utan att kringgå
periodlåset – vilket skulle bryta CLAUDE.md:s invariant att låsta intervall gäller *alla*
skrivningar.

**Beslutad ordning (Mattias 2026-08-16):**

1. Wizardens förkontroll kräver att `is_range_locked(company, year.start_date,
   dagen_före_sista_perioden)` – dvs. allt utom årets sista period är låst. Praktiskt villkor:
   `year_lock_status` får inte vara `"open"`, och luckan (om någon) får bara ligga i slutet av
   året, så att bokslutsverifikationens datum (årets sista dag) fortfarande är skrivbart.
2. Bokslutsverifikationen bokförs (steg 2).
3. **Därefter** körs helårslåsningen som wizardens sista steg, via befintlig
   `bookkeeping/views/period_locks.py::period_lock_lock_year` (URL
   `rakenskapsar/<int:pk>/las-hela-aret/`, gated på `period_lock.manage` = `finance_admin`), som
   redan skapar en enda `PeriodLock` med `reason="Helårslåsning (bokslut)"` – uttryckligen skriven
   med bokslut i åtanke. Ingen ny låsmekanism, och ingen bypass av `is_date_locked` någonstans.

### Balanskontroll – observera att "balanserar" inte betyder differens noll före bokslutet

`bookkeeping/reports.py::build_balance_sheet_context` beräknar
`balance_difference = total_assets - total_equity_and_liabilities` från klass 1–2-konton
(`BALANCE_SHEET_ACCOUNT_CLASSES = ["1", "2"]` i `bookkeeping/sie.py`).
`build_income_statement_context` beräknar `annual_result` från klass 3–8/9/10-konton
(`AccountClass.REVENUE` … `AccountClass.YEAR_END` i `bookkeeping/models.py`), filtrerat på
`transaction__accounting_year=selected_year` (per år, inte kumulativt).

Innan bokslutsverifikationen är bokförd har konto 2099 (eller 2019/2010-serien, se nedan)
inga poster för året – varje intäkts-/kostnadstransaktion är balanserad mot sitt eget motkonto
(bank, kundfordran, leverantörsskuld …) som redan ligger i klass 1–2 och redan syns i
`total_assets`/`total_liabilities`. Det gör att **`balance_difference` normalt inte är noll före
bokslutet**. Kontrollen i wizarden ska jämföra `balance_difference` mot `annual_result` från
resultaträkningen, inte kräva `balance_difference == 0`.

**Formel/tecken, verifierat mot koden (2026-08-16):** kontrollen är
`balance_difference == annual_result` – **samma tecken.** Härledning:
balansräkningens kontosaldon är `debet − kredit` (`get_account_balances`, reports.py rad ~88),
`total_equity_and_liabilities = -(total_equity + total_liabilities)` (rad 137),
`balance_difference = total_assets − total_equity_and_liabilities` (rad 138) – dvs. summan av
`debet − kredit` över alla klass 1–2-konton. Resultaträkningens belopp per konto är
`kredit − debet` (rad 177). Eftersom varje verifikation balanserar är klass 1–2-summan
(`debet − kredit`) alltid lika med klass 3–10-summan (`kredit − debet`) = `annual_result`.
Räkneexempel: försäljning 100 (debet 1930 / kredit 3001) ⇒ `balance_difference = 100`,
`annual_result = 100`. Efter bokslutsverifikation (debet 8999 / kredit 2099) ⇒ båda 0.

**Viktigt förbehåll – år måste stängas i ordning:** balansräkningen filtrerar på
`transaction__date <= end_date` över **alla** år (kumulativt), medan resultaträkningen filtrerar
på `transaction__accounting_year` (bara valt år). Likheten ovan gäller därför bara om alla
tidigare räkenskapsår redan är avslutade (deras resultat flyttat till eget kapital) eller tomma.
För befintliga företag – där bokslutsverifikationer aldrig kunnat skapas – innehåller
`balance_difference` ackumulerade obokade resultat från *alla* tidigare år. **Beslut:** wizarden
avslutar år i kronologisk ordning – förkontrollen kräver att varje tidigare `AccountingYear` har
en `TransactionSource.YEAR_END`-verifikation **daterad på det årets sista dag** (S1; datumvillkoret
skiljer den från nästa års S2-omföring) eller saknar poster helt, innan årets bokslut kan
påbörjas. Då gäller `balance_difference == annual_result` exakt.

## Steg 2: Bokslutsverifikationen

### Vad den ska göra, principiellt

Ett dubbelbokfört bokslut kräver att resultatet flyttas från resultaträkningens konton (klass
3–8/9/10) till balansräkningens eget kapital (klass 2). Eftersom
`build_income_statement_context` beräknar resultatet **per räkenskapsår** (filtrerar på
`transaction__accounting_year`, inte på datum kumulativt) behöver de enskilda intäkts-/
kostnadskontona **inte nollställas** rad för rad som i traditionell bokföring – nästa års
resultaträkning exkluderar automatiskt årets transaktioner eftersom den frågar på en annan
`AccountingYear`. Det som saknas är en enda balanserad verifikation som för in nettobeloppet i
eget kapital.

Ett balanserat dubbelbokfört bokslut kräver ändå att **en motpost utanför klass 1–2** finns – annars
går inte verifikationen ihop (den kan inte bara flytta pengar mellan två klass 2-konton, för då
förblir `total_equity` opåverkad och balansdifferensen kvarstår). Det naturliga BAS-kontot för
detta är **8999 "Årets resultat"** (klass 8 i detta system, `Account.account_class == "8"`,
`sru_code 7450`, finns redan i `bookkeeping/data/bas_2026_accounts.json`).

**Beslutat (Mattias 2026-08-16): två verifikationer, inte en.** En enda verifikation som bokar
hela vägen till 2091/2010 på årets sista dag gör att det stängda årets balansräkning aldrig
visar "Årets resultat" som egen rad – allt är redan konsoliderat, vilket avviker från hur en
K2-årsredovisning ställer upp eget kapital. Därför:

```
S1 – årets sista dag, stängda året (resultatöverföringen):
  Vinst:   Debet  8999   Kredit 2099   (beloppet = årets resultat; EF: 2019 i stället för 2099)
  Förlust: Debet  2099   Kredit 8999

S2 – första dagen, nästa år (omföringen):
  AB: Debet 2099 / Kredit 2091 (vinst; omvänt vid förlust)
  EF: hela 2011–2019-serien (konton med saldo) nollställs mot 2010
```

Det stängda årets balansräkning visar därmed "Årets resultat" på 2099/2019 (som
Fortnox/Visma), och nästa års balansräkning visar beloppet i 2091 respektive konsoliderat 2010
från dag ett. Båda verifikationerna får `TransactionSource.YEAR_END`; S1 tillhör det stängda
året, S2 nästa år – därför måste "är året avslutat?"-kontrollen filtrera på
**`YEAR_END`-verifikation daterad på årets sista dag** (S1), annars markerar S2 felaktigt nästa
år som avslutat.

**Verifierat och beslutat (2026-08-16):** 8999 ligger i `AccountClass.FINANCIAL` ("8") som ingår
i `financial_rows`/`total_financial` i `build_income_statement_context`, så när
bokslutsverifikationen är bokförd (daterad inom året) driver 8999-posten `annual_result` till
exakt noll – bekräftat i beräkningslogiken (se räkneexemplet i balanskontroll-avsnittet ovan).
**Beslut: behåll beteendet** – samma som Fortnox/Visma: resultaträkningen för ett avslutat år
visar 0 med 8999-raden "Årets resultat" som förklarande post. Ingen filtrering av
`TransactionSource.YEAR_END` i rapporten. Resultaträknings-templaten får en infotext som visas
när året har en bokslutsverifikation ("Året är avslutat – årets resultat är överfört till eget
kapital"), så nollan inte upplevs som ett fel. Ett test låser beteendet (resultat före bokslut =
V, efter bokslut = 0).

### AB (aktiebolag): 2099 → 2091/2098

Från `bas_2026_accounts.json`, klass 2-konton relevanta för AB:

| Konto | Namn | SRU-kod |
|---|---|---|
| 2081 | Aktiekapital | 7301 |
| 2090 | Fritt eget kapital | 7302 |
| 2091 | Balanserad vinst eller förlust | 7302 |
| 2098 | Vinst eller förlust från föregående år | 7302 |
| 2099 | Årets resultat | 7302 |

AB-flödet följer tvåverifikationsmodellen ovan: S1 bokar `annual_result` 8999 → 2099 på årets
sista dag, S2 för om 2099 → 2091 på nästa års första dag. Det som **inte** ingår i v1 är den
formella vinstdispositionen enligt årsstämmans beslut (utdelning, avsättning till reservfond,
"ny räkning") – S2 gör alltid hela beloppet till 2091 ("balanseras i ny räkning"), riktat mot
den K2-vänliga ambitionsnivån i `docs/feature-roadmap.md` ("hålla systemet enkelt"). 2098 hålls
som ett manuellt alternativkonto snarare än ett automatiskt val i v1 – en användare som vill
dela upp dispositionen gör det med en vanlig manuell verifikation i det nya året.

Ett formellt "vinstdispositionssteg" (utdelning till 2898, avsättning till reservfond 2086 etc.)
är uttryckligen **utanför v1** och blir i så fall ett eget, senare designbeslut – avgränsningen
dokumenteras i användarguiden.

### Enskild firma: 2010-serien

Från `bas_2026_accounts.json`:

| Konto | Namn |
|---|---|
| 2010 | Eget kapital |
| 2011 | Egna varuuttag |
| 2013 | Övriga egna uttag |
| 2017 | Årets kapitaltillskott |
| 2018 | Övriga egna insättningar |
| 2019 | Årets resultat, delägare 1 |

Till skillnad från AB finns ingen uppdelning mellan "aktiekapital" och "balanserat resultat" – allt
samlas i ett enda kapitalkonto (2010). Standardmönstret för enskild firma är att samtliga
"flödeskonton" för året (2011, 2013, 2017, 2018, 2019) nollställs mot 2010 vid bokslut, så att
nästa år börjar med ett enda konsoliderat 2010-saldo och tomma flödeskonton.

**Beslutat (Mattias 2026-08-16): hela 2011–2019-serien nollställs.** S1 bokar årets resultat
8999 → 2019 på årets sista dag. S2 (nästa års första dag) nollställer varje flödeskonto i
serien (2011, 2013, 2017, 2018, 2019) **som har saldo ≠ 0** mot 2010; konton utan poster hoppas
över (ingen tom rad). Det gör flödet robust oavsett om användaren bokfört uttag/insättningar mot
flödeskontona eller direkt mot 2010 löpande – i det senare fallet blir S2 bara 2019 → 2010.
Det stängda årets balansräkning visar uttag/insättningar/resultat specificerade per konto;
nästa år öppnar med ett konsoliderat 2010-saldo och tomma flödeskonton, enligt standard svensk
praxis.

`bas_2026_accounts.json` innehåller också en spegelserie 2020/2021/2023/2027/2028/2029 ("…
delägare 2") – ett tecken på att kontoplanen redan har stöd för handelsbolag/kommanditbolag med
flera delägare. **Beslut:** v1 av bokslutsflödet omfattar uttryckligen bara AB och enskild firma
(en ägare) enligt roadmapens formulering; handelsbolag/kommanditbolag med flera delägares
kapitalkonton lämnas utanför scope och kräver eget designbeslut om det efterfrågas senare.

### Bolagsform: nytt fält på `Company` (beslutat)

Sökning i `bookkeeping/models.py` (och övriga appar) efter `bolagsform`, `company_type`,
`CompanyType`, `company_form`, `legal_form` gav inga träffar – `Company`
(`bookkeeping/models.py`, rad ~25) har inget fält för bolagsform idag. Utan det vet wizarden inte
om den ska köra AB-flödet (2099 → 2091) eller enskild firma-flödet (2019 → 2010).

**Beslutat (Mattias 2026-08-16): nytt `TextChoices`-fält på `Company`**, t.ex.
`Company.LegalForm` med värdena `aktiebolag` / `enskild_firma`, samma mönster som
`Company.VatReportingPeriod`/`EmailProvider` i samma modell. Kräver en liten migration; fältet
sätts i företagsinställningarna (`CompanyForm`) och valideras därmed en gång i stället för vid
varje bokslut. `bookkeeping.company` är redan spårat i auditloggen, så ändringar av bolagsform
loggas automatiskt. Handelsbolag/ekonomisk förening läggs **inte** till som val i v1 (se
scope-beslutet ovan) – nya värden kan läggas till i `TextChoices` utan migration när det blir
aktuellt. Bortvalda alternativ: härledning från `org_number` (fritextfält utan validering –
gissningsbaserat) och fråga-i-wizarden-varje-gång (ingen lagring, ingen återanvändbarhet).

Befintliga företag får fältet tomt/`null` efter migrationen – wizarden kräver att bolagsform är
satt innan flödet kan startas och länkar till företagsinställningarna om den saknas. Ingen
default gissas.

### Ny transaktionskälla

`Transaction.source` (`TransactionSource` i `bookkeeping/models.py`) har idag ingen post för
bokslut. Ett nytt värde, t.ex. `TransactionSource.YEAR_END = "year_end"`, med en egen rad i
`DEFAULT_VOUCHER_SERIES_BY_SOURCE` (befintliga serier: A manuell, B bank, F kundfaktura, L
leverantörsfaktura, U utlägg, P lön, M moms, T anläggningstillgång, I SIE-import) gör att
bokslutsverifikationerna syns separat i huvudboken. **Beslutat: serie `S`** ("slutverifikation/
bokslut") – verifierat ledig i `DEFAULT_VOUCHER_SERIES_BY_SOURCE`. Källan används också av
förkontrollens "är tidigare år avslutade?"-fråga: ett år räknas som avslutat om det har en
`TransactionSource.YEAR_END`-verifikation daterad på årets sista dag (S1 – datumvillkoret
utesluter nästa års S2). Detta är litet och följer exakt samma mönster som
befintliga källor – ingen ny mekanik, bara ett nytt `TextChoices`-värde plus en rad i
default-mappningen och `VoucherSeriesRule.seed_defaults_for_company`.

## Steg 3: IB-överföring till nästa räkenskapsår

Per roadmapen: **IB beräknas redan dynamiskt.** Bekräftat i koden:

- `bookkeeping/sie.py::_account_net_amounts_by_id` aggregerar `JournalEntry`-poster med
  `upto_date`-filter – används både av SIE4-exportens `#IB`-rader
  (`generate_sie4_content`, rad ~266: `upto_date=day_before_start`) och av
  `reconcile_closing_balances`.
- `bookkeeping/reports.py::build_general_ledger_context` (huvudboken,
  `bookkeeping:general_ledger`, commit `c762b26`) beräknar `opening_balances` för valt
  räkenskapsår på samma sätt: `upto_date=selected_year.start_date - timedelta(days=1)`.

Ingen lagrad "IB-tabell" finns eller behövs – nästa års ingående balans är alltid summan av alla
tidigare bokförda poster fram till dagen innan årets start. **Det som saknas är just
bokslutsverifikationen (steg 2)** – utan den saknas 2099/2091/2010-postens bidrag till nästa års
IB, så nästa räkenskapsårs balansräkning öppnar utan årets resultat i eget kapital tills
verifikationen är bokförd. Så snart den är bokförd (daterad på räkenskapsårets sista dag) plockar
samma `_account_net_amounts_by_id`-logik upp den automatiskt vid nästa periods öppningsbalans –
ingen ny beräkningskod behövs.

**Beslutat (Mattias 2026-08-16): nästa räkenskapsår skapas inline i wizarden om det saknas.**
Wizarden föreslår `end_date + 1 dag` → `+1 år` och skapar `AccountingYear` med ett klick som en
del av flödet (`AccountingYear.clean()` validerar bara `end_date >= start_date`, ingen
kontinuitetsvalidering finns idag, så inget hindrar detta). Nästa år måste finnas innan S2 kan
bokföras (omföringen dateras på nästa års första dag), så detta ligger före
verifikationssteget i wizardens ordning, inte efter.

IB-steget blir därefter **en bekräftelsevy**, inte en skrivoperation: visa nästa års beräknade
öppningssaldon per konto (samma frågemönster som `build_general_ledger_context`s
`opening_balances`), så användaren kan se att bokslutsverifikationerna fick avsedd effekt innan
helårslåsningen. Notera att öppningsbalansen (dagen före nästa års start) visar resultatet på
2099/2019 – S2-omföringen till 2091/2010 är daterad på nästa års *första* dag och syns i nästa
års saldon, inte i IB-raden. Bekräftelsevyn bör visa båda.

## Ångra bokslut

**Beslutat (Mattias 2026-08-16): ingen egen ångra-funktion i v1.** Den manuella vägen finns
redan och räcker: en `finance_admin` låser upp perioden (`PeriodLock`-hantering) och skapar en
korrigeringsverifikation av S1/S2 via befintligt `correction_of`-mönster – allt spårat i
auditloggen. Wizarden tillåter omkörning av ett år vars `YEAR_END`-verifikationer är
korrigerade (kontrollen "är året avslutat?" räknar bara verifikationer som inte är korrigerade,
samma `correction_of__isnull=True, corrections__isnull=True`-filter som rapporterna redan
använder). Vägen dokumenteras i användarguiden.

## Byggstenar att återanvända

- `bookkeeping/models.py::AccountingYear`, `PeriodLock`, `Transaction`, `JournalEntry`,
  `TransactionSource`, `VoucherSeriesRule` – inga nya modeller för kärnflödet, förutom det nya
  `TransactionSource`-värdet och (beroende på beslut ovan) ett bolagsform-fält på `Company`.
- `bookkeeping/period_locking.py::year_lock_status`, `is_range_locked` – kontroll innan avslut.
- `bookkeeping/views/period_locks.py::period_lock_lock_year` – helårslåsning, redan skriven med
  "Helårslåsning (bokslut)" som anledningstext.
- `bookkeeping/reports.py::build_balance_sheet_context`, `build_income_statement_context`,
  `build_general_ledger_context` – balanskontroll, resultatberäkning, IB-bekräftelse. Ingen av
  dessa behöver ändras för att flödet ska fungera; enda rapportändringen är infotexten i
  resultaträknings-templaten för avslutade år (se 8999-beslutet ovan).
- `Transaction._assign_next_voucher_number`/`VoucherSeriesRule.resolve_series_code` – automatisk
  serietilldelning, samma mönster som alla andra verifikationskällor använder redan.
- Mönstret "generera verifikationer programmatiskt" som redan finns för
  `verification_template_catalog.py`/`bookkeeping/views/verification_templates.py` (nämnt i
  roadmapens punkt 2, Periodiseringar) är den närmaste befintliga koden för "skapa en
  `Transaction` + `JournalEntry`-rader från kod, inte från ett formulär" och bör användas som
  utgångspunkt snarare än att uppfinna ett nytt mönster.

## Behörighet

Hela flödet gatas bakom **`finance_admin`**, i linje med hur andra oåterkalleliga/compliance-
kritiska åtgärder redan är gatade i `bookkeeping/compliance_policy.py::ACTION_ROLE_MATRIX`
(`period_lock.manage`, `accounting_year.delete`, `export.sie4` m.fl. ligger redan på
`finance_admin`; endast `restore.dry_run` kräver `system_admin`). Ny nyckel att lägga till, t.ex.:

```python
ACTION_ROLE_MATRIX = {
    ...
    "accounting_year.close": "finance_admin",
}
```

använd via `@require_compliance_action("accounting_year.close")` på wizardens vyer, samma mönster
som `period_lock_lock_year`. `docs/compliance/role-matrix.md` måste uppdateras i samma ändring
(den listar redan varje nyckel i `ACTION_ROLE_MATRIX` parallellt, se befintliga rader för
`period_lock.manage` m.fl.).

## Revisionsspår (auditlog)

`Transaction` och `JournalEntry` är redan spårade i `auditlog/services.py::TRACKED_MODELS`
(`"bookkeeping.transaction"`, `"bookkeeping.journalentry"`, med `company_path` som redan pekar
rätt). Bokslutsverifikationen är strukturellt bara ytterligare en `Transaction`/`JournalEntry`-
uppsättning – den täcks alltså automatiskt av det hash-kedjade revisionsspåret utan någon ändring
i `auditlog/`. Om beslutet ovan landar i ett nytt fält `Company.legal_form`/bolagsform, är
`bookkeeping.company` redan spårat också (`TRACKED_MODELS["bookkeeping.company"]`), så även det
täcks automatiskt.

## Tidigare öppna frågor – nu avgjorda

Alla nio öppna frågor från dokumentets första version är avgjorda 2026-08-16 (kodverifiering +
beslut av Mattias, se respektive avsnitt och Beslutslogg):

1. **Bolagsform** → nytt `TextChoices`-fält på `Company` (se avsnittet ovan).
2. **Balanskontrollens formel** → `balance_difference == annual_result`, samma tecken, verifierad
   mot koden med härledning och räkneexempel. Nytt krav som följde av verifieringen: **år stängs i
   kronologisk ordning** (balansräkningen är kumulativ över år, resultaträkningen per år).
3. **8999-beteendet** → verifierat: resultaträkningen visar 0 efter bokslut. Beslut: behåll +
   infotext i rapporten, ingen filtrering.
4. **Enskild firma-konton** → hela 2011–2019-serien (konton med saldo) nollställs mot 2010.
5. **Formell vinstdisposition för AB** → utanför v1; S2 för alltid om hela beloppet till 2091
   ("balanseras i ny räkning"); avgränsningen dokumenteras i användarguiden.
6. **Förlust** → samma flöde med omvänd debet/kredit; ingen kontrollbalansräknings-varning
   (ABL 25 kap.) i v1 – SaldoVibe är inte ett juridiskt rådgivningsverktyg.
7. **Handelsbolag/kommanditbolag** → utanför scope v1; eget framtida designbeslut.
8. **Seriebokstav** → `S`, verifierad ledig.
9. **Dokumentation** → `docs/compliance/role-matrix.md` och `docs/user-guide/` uppdateras i samma
   ändring som koden (kvarstår som implementationskrav, inte en öppen fråga).

Dessutom upptäckt och löst vid granskningen: **låsordnings-motsägelsen** (helårslåsning måste
komma *efter* bokslutsverifikationerna, inte före – se Steg 1).

Vid andra granskningsrundan (samma dag) avgjordes ytterligare tre punkter:

10. **En eller två verifikationer** → två: S1 (resultatöverföring, årets sista dag) + S2
    (omföring, nästa års första dag), så att det stängda årets balansräkning visar "Årets
    resultat" som egen rad enligt K2-uppställningen. "Avslutat år"-kontrollen filtrerar på
    S1:s datum (årets sista dag) för att inte räkna S2.
11. **Ångra bokslut** → ingen egen funktion i v1; manuell väg (lås upp + korrigering)
    dokumenteras, wizarden tillåter omkörning när `YEAR_END`-verifikationerna är korrigerade.
12. **Nästa räkenskapsår** → skapas inline i wizarden (föreslagen period `end_date + 1 dag` →
    `+1 år`), före verifikationssteget eftersom S2 dateras i nästa år.

## Beslutslogg

- **Ingen formell vinstdispositionsmodul i v1**: S2 bokar alltid hela beloppet till 2091
  ("balanseras i ny räkning"); utdelning/reservfond görs som manuell verifikation i nya året.
  Matchar roadmapens uttalade ambitionsnivå ("enkel och användbar"). Medvetet uteslutet, inte
  bortglömt.
- **Ingen ny lagrad IB-tabell**: `_account_net_amounts_by_id`/huvudbokens dynamiska
  saldoberäkning återanvänds oförändrad; bokslutsflödets enda skrivoperation är
  bokslutsverifikationen själv (plus ev. helårslåsning).
- **`finance_admin`, inte `system_admin`**: bokslutsverifikationen är en vanlig, spårad
  `Transaction` inom ett företags egen bokföring (jämför `retention-purge-design.md`s val av
  `system_admin` för `retention.purge`, som destruerar räkenskapsinformation permanent – det här
  flödet skapar snarare en verifikation, med samma reversibilitetsnivå som övrig kontering, och
  matchar därför `period_lock.manage`/`export.sie4`-nivån).
- **Scope v1 = AB + enskild firma (en ägare) only**: handelsbolag/kommanditbolag och formell
  vinstdisposition för AB lämnas uttryckligen utanför, se punkterna 5 och 7 ovan.
- **(2026-08-16, Mattias)** **Bolagsform = nytt `TextChoices`-fält på `Company`**, satt i
  företagsinställningarna; wizarden vägrar starta om det saknas. Inget gissande från orgnummer.
- **(2026-08-16, Mattias)** **Låsordning: verifikation först, helårslåsning sist.** Förkontrollen
  kräver att allt utom årets sista period är låst; helårslåsningen är wizardens sista steg.
  Ingen bypass av `is_date_locked`.
- **(2026-08-16, Mattias)** **Enskild firma: hela 2011–2019-serien** (konton med saldo)
  nollställs mot 2010 i bokslutsverifikationen.
- **(2026-08-16, Mattias)** **Resultaträkningen efter bokslut visar 0** (8999-raden kvar), med
  infotext i rapporten; `TransactionSource.YEAR_END` filtreras inte bort.
- **(2026-08-16, granskning)** **År stängs i kronologisk ordning** – förkontrollen kräver att
  tidigare år har en `YEAR_END`-verifikation eller saknar poster, annars stämmer inte
  balanskontrollen (kumulativ balansräkning vs. per-år-resultaträkning).
- **(2026-08-16)** **Seriebokstav `S`** för `TransactionSource.YEAR_END`.
- **(2026-08-16, Mattias, runda 2)** **Två verifikationer i stället för en**: S1
  (8999 → 2099/2019, årets sista dag) + S2 (2099 → 2091 resp. 2011–2019 → 2010, nästa års
  första dag), så det stängda årets balansräkning visar "Årets resultat" enligt
  K2-uppställningen.
- **(2026-08-16, Mattias, runda 2)** **Ingen ångra-funktion i v1** – manuell väg via
  periodupplåsning + korrigeringsverifikation, dokumenterad i användarguiden.
- **(2026-08-16, Mattias, runda 2)** **Nästa räkenskapsår skapas inline i wizarden** om det
  saknas, före verifikationssteget (S2 behöver det).
