# 2. Löpande bokföring

Förutsättning: minst ett [räkenskapsår](01-komma-igang.md#räkenskapsår) måste finnas för det
aktiva företaget. Saknas det blockeras både ny verifikation och SIE-import med ett felmeddelande
som länkar dig vidare till **Inställningar → Räkenskapsår**.

## Registrera en verifikation

1. Klicka **Ny verifikation** (snabbknappen högst upp i vänstermenyn, eller
   **Bokföring → Verifikationer → Ny verifikation**).
2. Fyll i datum (förvalt till dagens datum) och en beskrivning.
3. Lägg till konteringsrader: minst två rader krävs. Varje rad är antingen debet **eller** kredit –
   aldrig båda på samma rad.
4. Summan av debet måste vara exakt lika med summan av kredit innan verifikationen går att spara.
   Är den inte i balans visas exakt mellanskillnaden i felmeddelandet.
5. Datumet måste falla inom exakt ett räkenskapsår. Om inget eller flera räkenskapsår matchar
   datumet får du ett felmeddelande istället för att verifikationen sparas.
6. Är perioden för det valda datumet **låst** (se periodlåsning i Inställningar) avvisas
   registreringen med "Den valda perioden är låst. Skapa en korrigeringsverifikation i öppen
   period eller lås upp perioden."

Endast aktiva konton (se [Kontoplan](01-komma-igang.md#kontoplan)) går att välja på en
konteringsrad.

## Verifikationsmallar

Används för återkommande bokföringar (t.ex. samma motpartskonton varje månad).

1. Gå till **Bokföring → Verifikationsmallar**.
2. Skapa en mall med namn, valfri beskrivning och minst två rader. Varje rad markeras som antingen
   debet eller kredit (exakt en av de två – formuläret validerar detta).
3. När du sedan skapar en ny verifikation och väljer mallen förifylls kontona automatiskt på
   konteringsraderna (belopp måste du fortfarande fylla i manuellt per verifikation).

## Periodisering

Bokar en kostnad eller intäkt över flera månader i ett svep – t.ex. en försäkringspremie eller
hyra som ska fördelas jämnt. Skapar N verifikationer direkt, en per månad; det finns ingen
bakgrundskörning och ingen automatisk upplösning/reversering senare (vill du bokföra bort en
periodisering görs det som en vanlig [korrigering](#korrigera-en-verifikation) av respektive
verifikation).

1. Gå till **Bokföring → Periodisering** (eller knappen **Periodisering** på
   verifikationslistan).
2. Fyll i beskrivning, totalt belopp, **Konto** (kostnads- eller intäktskontot, t.ex. 6310
   Försäkringar) och **Motkonto**:
   - Är kontot ett **kostnadskonto** måste motkontot vara ett **17xx**-konto (förutbetald
     kostnad), t.ex. 1730 Förutbetalda försäkringspremier.
   - Är kontot ett **intäktskonto** måste motkontot vara ett **29xx**-konto (upplupen kostnad
     och förutbetald intäkt), t.ex. 2970 Förutbetalda hyresintäkter.

     Väljer du fel kombination avvisas formuläret med ett felmeddelande som talar om vilken
     kontoserie som krävs.
3. Ange **antal månader** (2–60) och **startmånad** – dagen i datumet spelar ingen roll, bara
   vilken månad den faller i.
4. Varje verifikation dateras till sista dagen i sin månad och får beskrivningen
   `<din beskrivning> (n/N)`. Beloppet delas jämnt över månaderna; eventuell
   öresmellanskillnad läggs på den sista verifikationen så att summan alltid stämmer exakt
   mot totalbeloppet.
5. Precis som vid vanlig registrering kontrolleras varje enskild månads datum mot periodlås och
   räkenskapsår **innan** något skapas – täcker någon av de N månaderna en låst period, eller
   saknar/har flera räkenskapsår som matchar, skapas ingenting alls och du får ett
   felmeddelande per problemmånad.

## Transaktionslistan

**Bokföring → Verifikationer** visar alla verifikationer för aktivt företag, senaste först.
Filtrera på:

- **Räkenskapsår** – growlistan visar bara det årets verifikationer.
- **Konto** – visar bara verifikationer som har minst en rad på det valda kontot.

## Korrigera en verifikation

SaldoVibe tillåter inte att en bokförd verifikation ändras i efterhand. Istället skapas en
**korrigeringsverifikation**:

1. Öppna verifikationen och välj **Korrigera**.
2. Systemet skapar automatiskt en ny verifikation daterad till idag, med alla debet/kredit-belopp
   från originalet vända (debet blir kredit och tvärtom), kopplad till originalet.
3. Både originalet och korrigeringen ligger kvar i transaktionslistan. Öppnar du någon av dem
   visas en gul länk i sidfoten ("Korrigerad av …" respektive "Korrigering av …") som tar dig
   till den andra verifikationen.
4. En verifikation kan bara korrigeras **en gång** – försöker du igen får du meddelandet
   "Verifikationen har redan en registrerad korrigering." Knappen **Skapa korrigering** visas
   därför inte på verifikationer som redan är korrigerade, eller på korrigeringsverifikationer.
5. Precis som vid vanlig registrering blockeras korrigeringen om dagens datum ligger i en låst
   period, eller om inget/flera räkenskapsår matchar dagens datum.

## Importera från annat bokföringssystem

### SIE-import (`Verifikationer → Importera SIE`)

- Kräver att ett räkenskapsår redan finns; import sker mot det valda räkenskapsåret.
- Finns tidigare SIE/SI-importerade verifikationer i räkenskapsåret ersätts de av filens innehåll
  – du måste först kryssa i en bekräftelseruta. Verifikationer som bokförts direkt i SaldoVibe
  (manuellt, via faktura, lön m.m.) och verifikationer i låsta perioder tas aldrig bort av en
  import.
- Filen läses med automatisk teckenkodningsdetektering (utf-8-sig, cp437, latin-1).
- Vid import räknas och rapporteras: importerade, dubbletter (samma serie+nummer+datum finns
  redan), verifikationer utanför räkenskapsårets datumintervall, verifikationer i låsta perioder,
  och obalanserade verifikationer – alla dessa hoppas över utan att stoppa hela importen.
- Konton som saknas i kontoplanen skapas automatiskt (med kontoklass gissad från första siffran i
  kontonumret) så att importen inte fastnar på okända konton.
- Filens **ingående balanser** (`#IB 0`-rader) bokförs som en egen verifikation "Ingående balans"
  (referens `IB`) på räkenskapsårets första dag – praktiskt när du flyttar in från ett annat
  program. Det sker bara om det inte redan finns bokföring före räkenskapsårets start, om ingen
  IB-verifikation redan finns och om raderna balanserar; annars hoppas de över med ett meddelande.
  En fil som bara innehåller `#IB`-rader (inga verifikationer) går också att importera.
- Hittar valideringen radfel i filen visas en diagnostikvarning, och en detaljerad
  diagnostikrapport kan laddas ner separat.

### SI-import

En enklare importväg för SI-filer (`Verifikationer → Importera SI`), med motsvarande
diagnostiknedladdning vid fel.

Både SIE- och SI-import kräver behörigheten `import.sie` (se rollmatrisen i
`docs/compliance/role-matrix.md`), eftersom en import kan skapa konton och ersätta tidigare
importerade verifikationer.

## Exportera till SIE4

**Verifikationer → Exportera SIE4** genererar en fullständig SIE4-fil för valt räkenskapsår
(`SIE4_<organisationsnummer>_<år>.se`), som kan importeras i andra bokföringsprogram eller lämnas
till revisor. Filen skrivs i SIE-standardens teckenkodning (PC8/cp437); tecken som saknas där, t.ex.
tankstreck och typografiska citattecken, ersätts med närmaste motsvarighet (`-`, `'`). Åtgärden kräver behörigheten `export.sie4` (se rollmatrisen i
`docs/compliance/role-matrix.md`).

## Nästa steg

Kommande kapitel: Momsrapport, Bilagor, Bank & skattekonto – se [innehållsförteckningen](README.md).
