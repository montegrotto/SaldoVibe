# 10. Rapporter

## Balansräkning och resultaträkning

**Rapporter → Balansräkning / Resultaträkning** visar rapporterna i svensk, grupperad
uppställning för valt räkenskapsår, med möjlighet att exportera respektive rapport som **PDF**.

För ett [avslutat räkenskapsår](01-komma-igang.md#bokslut-årsavslut) visar resultaträkningen 0 –
årets resultat är överfört till eget kapital via bokslutsverifikationen (8999-raden står kvar som
förklarande post). En inforuta i rapporten förklarar detta: "Året är avslutat – årets resultat är
överfört till eget kapital".

## Resultatbudget

Du kan lägga en budget per konto och månad för resultaträkningens konton (kontoklass 3–10;
balanskonton har ingen budget). Öppna budgeten via knappen **Redigera budget** överst i
resultaträkningen, eller via knappen **Budget** på räkenskapsårets rad under **Inställningar →
Räkenskapsår**.

Budgetsidan visar ett rutnät med kontona som rader och årets tolv månader som kolumner. Fyll i
belopp per konto och månad; samma teckenkonvention som resultaträkningen gäller – intäkter anges
som positiva belopp, kostnader som negativa. En tom cell betyder att kontot saknar budget den
månaden. Klicka **Spara budget** för att spara, eller **Avbryt** för att gå tillbaka utan att
spara.

Rutnätet visar bara konton som antingen har bokförda rader på räkenskapsåret eller redan har en
budgetrad – inte hela kontoplanen. Vill du budgetera på ett konto som ännu inte använts, bokför
först en verifikation på kontot (t.ex. ett nollbelopp går inte, men en vanlig verifikation gör
att kontot dyker upp i rutnätet nästa gång du öppnar budgeten).

Så snart minst en budgetrad finns för det valda räkenskapsåret visar resultaträkningen (både på
skärm och i PDF-exporten) två extra kolumner per rad och delsumma: **Budget** (summan av radens
budgetrader för året) och **Diff** (utfall minus budget).

## Huvudbok

**Rapporter → Huvudbok** visar alla konton med bokförda belopp för valt räkenskapsår. Per konto
visas:

- **Ingående saldo** (för balanskonton med saldo från tidigare år).
- Årets verifikationsrader med datum, verifikationsnummer (klickbart, öppnar verifikationen),
  beskrivning, debet, kredit och **löpande saldo**.
- **Utgående saldo** med summerad debet och kredit.

Längst ner summeras årets totala debet och kredit, som alltid ska vara lika. Rapporten kan
exporteras som **PDF**.

## Reskontra

**Rapporter → Reskontra** visar obetalda bokförda fakturor per valfritt datum – som standard
dagens datum, men med datumväljaren **Per** kan du se ställningen per t.ex. ett månadsslut
eller ett bokslutsdatum. Fakturor räknas som obetalda per det valda datumet om betalningens
betalningsdatum ligger efter det, och avstämningen mot huvudboken görs per samma datum.
Rapporten har två delar: **Kundreskontra** (obetalda kundfakturor) och **Leverantörsreskontra**
(obetalda leverantörsfakturor). För varje del visas:

- Fakturorna med fakturanummer (klickbart), motpart, förfallodatum, antal dagar försenad och
  återstående belopp. Kreditfakturor minskar reskontran.
- En **åldersanalys** som fördelar återstående belopp på Ej förfallet, 1–30 dagar, 31–60 dagar,
  61–90 dagar och Över 90 dagar efter förfallodatum.
- En **avstämning** mellan reskontrans summa och det bokförda saldot på reskontrakontona
  (normalt 1510 respektive 2440). **Differens** ska vara 0,00 kr – en avvikelse betyder att
  något bokförts direkt på reskontrakontot utan koppling till en faktura.

Rapporten kan exporteras som **PDF** – exporten avser samma datum som visas på skärmen.

## SRU-rapport

**Rapporter → SRU-rapport** visar SRU-koder per konto för valt räkenskapsår, med en
**preflight-diagnostik** som varnar/felar på:

- Konton som helt saknar SRU-kod.
- Konton med en ogiltig SRU-kod.

Fixa de fel diagnostiken visar innan du exporterar – **Ladda ner** hämtar SRU-underlaget som en
zip-fil, och en separat diagnostikrapport kan laddas ner fristående för att stämma av innan
inlämning.

## Händelselogg (auditlogg)

**Rapporter → Händelselogg** listar alla spårade händelser (skapande, ändring, radering) för
aktivt företag, med filter på:

- Åtgärdstyp (skapa/ändra/radera).
- Modell/typ av objekt (t.ex. verifikation, faktura, bilaga – inklusive relaterade
  underobjekt som konteringsrader).
- Fritextsökning i sammanfattning, objektbeskrivning eller vem som utförde åtgärden.
- Datumintervall.

Varje händelse visar vem (aktör), vad (modell/objekt), när och en före/efter-vy av ändrade fält.
Loggen är kedjad med hashar (`prev_hash`/`entry_hash`) för att kunna upptäcka manipulation – se
integritetsverifiering i `docs/compliance/`.

## Compliance-översikt

**Rapporter → Compliance-översikt** ger en snabb hälsokontroll av bokföringen för valt
räkenskapsår:

- **Nummerluckor** i verifikationsserier (saknade verifikationsnummer inom en serie).
- **Sena bokföringar** – verifikationer där mer än 7 dagar gått mellan verifikationsdatum och
  registreringstillfället.
- Leverantörsfakturor som är registrerade men **saknar bilaga/underlag**.
- **Herrelösa bilagor** – uppladdade bilagor som inte är kopplade till någon leverantörsfaktura.
- Antal händelser senaste 30 dagarna som nämner låsning/period (indikerar t.ex. upprepade försök
  att bokföra i låst period).
- **Hashkedje-avvikelser** i auditloggen – räknar poster i företagets egen hashkedja där den
  beräknade hashen inte matchar den lagrade, vilket signalerar potentiell manipulation eller
  dataintegritetsproblem.
- **Momsstängningar där underlaget ändrats efter stängning** – varje stängd momsperiods
  källfingeravtryck räknas om och jämförs med det som sparades vid stängningen; en avvikelse
  betyder att bokföringen i perioden har ändrats efter att momsen stängdes.

Denna vy kräver att minst ett räkenskapsår finns; annars länkas du direkt till att skapa ett.
Eftersom sidan bland annat verifierar auditloggens hashkedja kräver den rollen **finance_admin**
(behörigheten `audit.verify_chain`, se rollmatrisen i `docs/compliance/role-matrix.md`).

## Exportera bokföring

**Rapporter → Exportera bokföring** låter dig starta ett komplett exportpaket (zip) för en
valfri period – du väljer start- och slutdatum fritt, oberoende av räkenskapsårens gränser.
Paketet innehåller:

- **Bokföringsdata** – kompletta SIE4-filer, en per räkenskapsår som täcker den valda perioden.
- **Kontospecifikationer** – en PDF per konto med bokföring under perioden, med ingående saldo,
  varje transaktionsrad och utgående saldo.
- **Bilagor** – alla bilagor som är relevanta för perioden, uppdelat i **bokförda** (kopplade till
  en verifikation, leverantörsfaktura, kundfaktura eller utlägg som bokförts under perioden) och
  **ej bokförda** (ännu inte bokförda, uppladdade under perioden).
- **Banktransaktioner** – en CSV per bankkälla (bankkonto, skattekonto osv.) med bankens egna
  transaktioner under perioden, oavsett om de har bokförts eller inte – så att bankens uppgifter
  kan stämmas av mot bokföringen i efterhand.
- **Rapporter** – regenererade PDF:er för moms, arbetsgivardeklaration (AGI) och lönebesked, men
  bara för perioder som redan är avslutade innan exporten körs: en PDF per momsperiod som stängts
  via **Bokföring → Momsrapport**, en PDF per lönekörning som markerats rapporterad till
  Skatteverket, och ett lönebesked per anställd på en avslutad lönekörning. En pågående
  momsperiod eller en ej rapporterad/avslutad lönekörning tas inte med – siffrorna kan fortfarande
  ändras, så paketet ska aldrig visa något som ofärdigt som filat.

Filerna knyts ihop av en bläddringsbar `index.html`-sida i paketet, samt en `manifest.json` med en
SHA256-kontrollsumma för varje fil – så att paketets integritet kan verifieras i efterhand.

Sidan visar en förhandsgranskning (antal räkenskapsår, bokförda/ej bokförda bilagor,
banktransaktioner per bankkälla, samt antal stängda momsperioder/rapporterade AGI-lönekörningar/
lönebesked) innan du startar exporten. Att starta och ladda ner ett exportpaket kräver rollen
**finance_admin** (eller motsvarande behörighet); vem som helst i företaget kan se sidan och
förhandsgranskningen.

**Exporten körs i bakgrunden** – klicket på "Starta export i bakgrunden" returnerar direkt utan
att du behöver vänta. Under rubriken **Mina exportpaket** listas alla tidigare startade exporter
för företaget med sin status (**Väntar**, **Pågår**, **Klar** eller **Misslyckades** – med
felmeddelande om exporten misslyckades). Sidan uppdaterar inte statusen live; uppdatera sidan
(F5) för att se när en pågående export blivit klar. När en export är klar dyker en notis upp i
klockikonen uppe till höger ("Exportpaket redo att laddas ner"), synlig på alla sidor i appen tills
du besöker exportsidan igen.

Varje klart exportpaket har en **Ladda ner**-knapp och stannar kvar, nedladdningsbart, tills du
själv tar bort det med **Ta bort**-knappen på samma rad – det finns ingen automatisk
gallring/utgångstid.

## Nästa steg

Fortsätt till [11. Företagsinställningar](11-foretagsinstallningar.md).
