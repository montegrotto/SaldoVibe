# 7. Kundfakturor

## Kunder och artiklar

- **Försäljning → Kunder**: skapa kunder med bl.a. namn och standardbetalningsvillkor (dagar), som
  förifyller förfallodatum på nya fakturor.
- **Försäljning → Artiklar**: varje artikel har á-pris, momssats och ett **intäktskonto**. Fakturarader
  måste alltid kopplas till en artikel – det är artikelns intäktskonto och momssats som styr
  bokföringen, inte fritext.

## Skapa en kundfaktura

1. Gå till **Försäljning → Kundfakturor → Ny faktura**.
2. Välj kund (förfallodatum/betalningsvillkor förifylls från kundens standardvärden).
3. Lägg till minst en rad: välj artikel (á-pris och moms fylls i automatiskt, men kan justeras) och
   ange antal.
4. Fakturan får ett automatiskt **fakturanummer** samt en **OCR-referens** med
   mod10-kontrollsiffra, för användning på inbetalningar.
5. Spara antingen som **utkast** eller direkt med **Skapa och bokför**.

## Bokföra en faktura

Bokföring kan göras direkt vid skapande eller senare från fakturans detaljvy (**Bokför**). Vid
bokföring:

- Perioden för fakturadatumet får inte vara låst, och exakt ett räkenskapsår måste matcha datumet.
- Kontot **1510** (kundfordran) måste finnas i kontoplanen.
- Varje faktura rad måste ha en artikel med intäktskonto ifyllt – saknas det avbryts bokföringen
  med ett tydligt felmeddelande om vilken artikel som saknar konto.
- Endast momssatserna **25 %, 12 % och 6 %** stöds i den automatiska bokföringen (mappas till
  2611/2621/2631); andra satser stoppas med felmeddelande.
- Bokföringen blir: **debet** kundfordran (1510) för totalbeloppet, **kredit** artiklarnas
  intäktskonton (exkl. moms), **kredit** utgående moms per momssats.
- Är fakturan redan bokförd görs ingen ny bokning ("redan bokförd").

## Registrera betalning

Betalningar som syns på banken bokförs normalt i Bank-vyn (se
[5. Bank & skattekonto](05-bank-skattekonto.md)). För övriga fall finns **Registrera betalning**
på fakturans detaljvy:

- Ange **betalningsdatum**, **betalt belopp** och **betalkonto** (förslag: 1930). Del- och
  helbetalningar stöds – fakturan markeras som fullt reglerad först när hela beloppet är täckt.
- **Avskrivet belopp** låter dig samtidigt skriva av en rest som inte kommer att betalas, till
  valfritt **avskrivningskonto** – öresavrundning (3740), konstaterad kundförlust (förslag: 6351)
  eller rabatt.
- Med **Justera utgående moms (kundförlust)** ibockad delas avskrivningen per momssats: momsdelen
  återtas på utgående moms-kontot (2611/2621/2631) och resten bokförs på avskrivningskontot.
- Betalning och avskrivning bokförs som en verifikation; perioden för betalningsdatumet får inte
  vara låst. Registrerade betalningar kan ångras via **Ångra betalning**, som skapar en
  korrigeringsverifikation.

## Kvitta kreditfaktura mot debetfaktura

När en kund har både en obetald debetfaktura och en obetald kreditfaktura kan de kvittas mot
varandra via **Kvitta mot faktura** på fakturans detaljvy. Välj motfaktura och kvittningsdatum –
det minsta återstående beloppet kvittas, en verifikation bokförs mellan fakturornas
fordringskonton och en betalningspost registreras på båda fakturorna. En kvittning ångras som
vilken betalning som helst, och båda fakturorna återställs då tillsammans.

## Kreditera en faktura

**Kreditera** skapar en ny kreditfaktura som speglar originalfakturans rader (kopplad till
originalet i referensen). Kräver att originalfakturan redan är bokförd och innehåller minst en
rad. En redan krediterad faktura kan inte krediteras igen.

## Skriva ut faktura

**Utskrift** genererar en PDF av fakturan för utskick till kund, inklusive OCR/QR-betalningsuppgifter.

## E-faktura (Peppol)

På fakturans detaljsida finns **Ladda ner e-faktura (Peppol XML)** som genererar fakturan i
Peppol BIS Billing 3.0-format (UBL/EN 16931) – formatet som krävs vid fakturering till
offentlig sektor enligt lagen om e-faktura (SFS 2018:1277). Saknas obligatoriska uppgifter
(t.ex. momsregistreringsnummer, adresser eller kundens referens) visas felen på svenska och
ingen fil genereras förrän de är åtgärdade.

SaldoVibe skickar inte fakturan i Peppol-nätverket – det kräver en accesspunkt
(e-fakturaväxel), vilket ligger utanför systemets integrationsfria linje. Ladda i stället upp
den nedladdade XML-filen manuellt hos din accesspunktsleverantör eller i köparens
leverantörsportal (många offentliga köpare erbjuder en portal där filen kan laddas upp direkt).

## Betalningspåminnelse

När en bokförd, obetald faktura har passerat sitt förfallodatum visas **Skriv ut påminnelse** på
fakturans detaljvy:

- Ange **påminnelseavgift** (förslaget hämtas från företagets inställning **Påminnelseavgift**,
  se [11. Företagsinställningar](11-foretagsinstallningar.md) – sätt 0 för ingen avgift) och
  **betala senast** (förslag: tio dagar framåt). Utskriften bekräftas innan påminnelsen
  registreras.
- Påminnelsen genereras som PDF i samma brevformat som fakturan (fönsterkuvert), med
  **påminnelsenummer**, fakturabelopp, eventuellt redan betalt belopp, återstående belopp,
  avgiften och summan **att betala**. QR-koden avser det nya beloppet och det nya betaldatumet.
- Varje utskrift registreras under **Skickade påminnelser** på detaljvyn, med nummer, datum,
  avgift och betala senast-datum. Därifrån kan en registrerad påminnelse skrivas ut igen utan
  att en ny skapas. Så länge fakturan är obetald kan nästa påminnelse skrivas ut – knappen
  visar numret den får (påminnelse 2, 3, ...).
- Efter två registrerade påminnelser visas en varning i påminnelserutan: nästa steg är normalt
  ett inkassovarsel eller inkassokrav snarare än fler påminnelser. Det går fortfarande att
  skriva ut fler påminnelser – varningen är en upplysning, inget stopp.
- Påminnelsen bokför ingenting – avgiften följer med som en upplysning på utskriften. Betalar
  kunden avgiften registrerar du den som en del av betalningen, t.ex. mot konto 3591
  (påminnelseavgifter).

## Skicka faktura och påminnelse via e-post

När företaget har **utgående e-post** konfigurerad (se
[11. Företagsinställningar](11-foretagsinstallningar.md#utgående-e-post)) och kunden har en
e-postadress registrerad kan fakturan och påminnelsen skickas direkt från detaljvyn:

- **Skicka faktura via e-post** visas för en bokförd faktura (ej kreditfaktura) och skickar
  fakturan som PDF-bilaga till kundens e-postadress, med ett kort följebrev på svenska.
- I påminnelserutan finns **Registrera och skicka via e-post** som registrerar påminnelsen
  (precis som utskriftsknappen) och skickar påminnelse-PDF:en till kunden i stället för att
  öppna den för utskrift.
- Varje utskick loggas under **Skickade e-postmeddelanden** på detaljvyn med datum, typ,
  mottagare och status. Misslyckade utskick visas med status **Misslyckad** (håll muspekaren
  över för felmeddelandet) och dyker även upp i notisklockan.
- Saknar kunden e-postadress, eller är utgående e-post inte konfigurerad, är knappen inaktiverad
  med en förklaring.

## Nästa steg

Fortsätt till [8. Leverantörsfakturor](08-leverantorsfakturor.md).
