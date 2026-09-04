# 6. Löner

## Lägg upp anställda

Gå till **Personal → Anställda** för att skapa en anställd. Nödvändiga uppgifter:

- Namn och **personnummer** (unikt per företag – samma person kan inte läggas upp två gånger).
  Av integritetsskäl visas personnumret maskerat (t.ex. `19900101-XXXX`) i listor och på
  lönekörningssidor; fullt personnummer förekommer bara i AGI-filen och på lönebeskedet.
- **Månadslön** och **sysselsättningsgrad (%)** – bruttolön i en lönekörning räknas som
  `månadslön × sysselsättningsgrad / 100`.
- **Skattetabell** (1–40) och **kolumn** (1–6) som styr preliminärskatteberäkningen.
- Anställningsdatum (valfritt) och om personen är aktiv.
- **E-post** (valfritt) – dit lönespecifikationen kan skickas, se nedan.

Du kan även lägga upp **standardjusteringar** per anställd (t.ex. återkommande tillägg/avdrag) som
automatiskt kopieras in på varje ny lönekörning för den personen.

## Skapa en lönekörning

1. Gå till **Personal → Löner → Ny lönekörning**.
2. Välj period (månad) i periodväljaren. Det går bara att ha **en** lönekörning per period och företag.
3. Kryssa i "generera lönerader" för att automatiskt skapa en lönepost per aktiv anställd
   (bruttolön beräknas från anställdas månadslön/sysselsättningsgrad, standardjusteringar kopieras
   in automatiskt).
4. Du kan även lägga till eller ta bort enskilda anställda i efterhand innan körningen avslutas.

## Justera enskilda löneposter

Öppna lönekörningen och redigera en lönepost för att justera bruttolön, lägga till tillägg/avdrag
(före eller efter skatt, skattepliktiga eller ej) eller ändra skattetabell/kolumn för just den
utbetalningen.

## Lönespecifikation – skriv ut eller skicka via e-post

På lönekörningens sida finns knappen **Lönespec** per lönepost som öppnar lönespecifikationen som
PDF. Har den anställde en e-postadress visas även en kuvertknapp: den skickar samma PDF till
adressen via företagets utgående e-postkonto (se
[11. Företagsinställningar](11-foretagsinstallningar.md#utgående-e-post)). Är utgående e-post
inte konfigurerad får du ett felmeddelande. Varje utskick loggas som skickad e-post med typen
*Lönespecifikation*.

## Avsluta lönekörningen

Detta är den återvändslösa delen av flödet – när en körning avslutas:

1. Preliminärskatt beräknas via **Skatteverkets API** för varje löneperson. Om anropet misslyckas
   avbryts hela avslutet (hård stoppning, inget "bästa gissning"-fallback).
2. Utbetalningsdatumets period måste matcha exakt ett räkenskapsår och får inte vara låst – annars
   avbryts avslutet med tydligt felmeddelande.
3. En bokföringsverifikation skapas automatiskt: lönekostnad (7010), arbetsgivaravgift (7510),
   skatteskuld (2710), avgiftsskuld (2731) och löneskuld (2910), plus eventuella
   justeringskonton.
4. Betalningspåminnelser skapas för lönerna.
5. En redan avslutad/rapporterad körning kan inte avslutas igen.

## Rapportera till Skatteverket (AGI)

1. **AGI-fil till Skatteverket** (XML) kan laddas ner så snart körningen är avslutad – filen är
   schemavalidet mot Skatteverkets "arbetsgivardeklaration på individnivå"-format och laddas upp
   av arbetsgivaren själv via Skatteverkets e-tjänst.
2. **Markera som rapporterad** låser körningen permanent mot vidare ändringar och skapar samtidigt
   ett **AGI-bevispaket**: en zip med manifest (SHA-256 av underlaget, tidsstämpel,
   organisationsuppgifter) och den fullständiga JSON-nyttolasten. Bevispaketet kan laddas ner i
   efterhand för revision.
3. Åtgärderna "markera som rapporterad" och "ladda ner bevispaket" kräver behörigheten
   `payroll.report_mark` (se `docs/compliance/role-matrix.md`).

## Nästa steg

Fortsätt till [7. Kundfakturor](07-kundfakturor.md).
