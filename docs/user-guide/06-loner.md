---
description: "Anställda, lönekörning och arbetsgivardeklaration på individnivå (AGI) till Skatteverket i SaldoVibe, inklusive evidens för inlämningen."
---

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
- **Kvarvarande semesterdagar** – saldot som semesteruttag dras från. Fyll på manuellt vid
  nytt semesterår (SaldoVibe räknar inte intjänande per intjänandeår).
- **E-post** (valfritt) – dit lönespecifikationen kan skickas, se nedan.

Du kan även lägga upp **standardjusteringar** per anställd (t.ex. återkommande tillägg/avdrag) som
automatiskt kopieras in på varje ny lönekörning för den personen.

## Skapa en lönekörning

1. Gå till **Personal → Löner → Ny lönekörning**.
2. Välj period (månad) i periodväljaren. Det går bara att ha **en** lönekörning per period och företag.
   Utbetalningsdatumet måste ligga inom ett befintligt räkenskapsår – saknas året stoppas
   formuläret, skapa då året under **Inställningar → Räkenskapsår** först.
3. Kryssa i "generera lönerader" för att automatiskt skapa en lönepost per aktiv anställd
   (bruttolön beräknas från anställdas månadslön/sysselsättningsgrad, standardjusteringar kopieras
   in automatiskt).
4. Du kan även lägga till eller ta bort enskilda anställda i efterhand innan körningen avslutas.

## Justera enskilda löneposter

Öppna lönekörningen och redigera en lönepost för att justera bruttolön, lägga till tillägg/avdrag
(före eller efter skatt, skattepliktiga eller ej) eller ändra skattetabell/kolumn för just den
utbetalningen.

### Utlägg som betalas ut med lönen

Har den anställde bokförda utlägg eller körrapporter som ännu inte betalats ut visas rutan
**Utlägg som väntar på utbetalning** överst på löneposten. Bocka i dem som ska följa med
löneutbetalningen – eller kryssa i **Markera/avmarkera alla** – och spara. Preliminära (obokförda) utlägg
visas inte, bokför dem först i utläggslistan. På lönekörningens sida lägger knappen
**Ta med alla utlägg** alla bokförda, obetalda utlägg och körrapporter som inte redan ligger på en
lönepost på respektive anställds lönepost, så slipper du bocka i dem en och en.

**Nettolönen är före utlägg.** Utlägg och körrapporter är skattefria ersättningar som läggs ovanpå
nettolönen vid utbetalning. Lönekörningens sida visar därför per anställd kolumnerna **Netto**,
**Utlägg** (med delsumma för körrapporter) och **Att utbetala** (= netto + utlägg), och överst
summorna för hela körningen: nettolön, utlägg och körrapporter samt totalt att utbetala.

På lönespecifikationen visas efter nettolönen en summerad rad **Utlägg** och en summerad rad
**Körrapporter** (de enskilda posterna listas inte), följt av raden **Att utbetala**. På utläggets
sida står att det betalas ut med lönen i den lönekörningen. Ett utlägg kan bara ligga på en lönepost
i taget. Bocka ur och spara för att ta bort det igen, eller ta bort den anställde från körningen.

## Körrapporter

Körrapporter finns under **Personal → Utlägg**, eftersom de är utlägg: klicka **Ny körrapport** och
fyll i anställd, datum, resväg, syfte, sträcka i km och ersättning i kr/mil (förifyllt med
Skatteverkets skattefria schablon, 25 kr/mil). Samma sak går att göra i
[mobilappen](13-mobilappen.md#körrapporter) direkt efter resan.

- **Lämna in och bokför** skapar och bokför ett utlägg på beloppet (`km / 10 × kr/mil`) med konto
  7331 (Skattefria bilersättningar) mot skulden 2820. Saknas något av kontona i kontoplanen, eller
  ett räkenskapsår för resdatumet, stoppas inlämningen.
- **Spara som utkast** skapar en preliminär körrapport som inte är bokförd. Bokför den senare med
  **Bokför** i utläggslistan, eller ta bort den medan den är ett utkast. Samma gäller vanliga
  utlägg (**Spara som utkast** i utläggsformuläret).

Körrapporter syns i utläggslistan med en bilikon och sträckan. Öppna utlägget med **Visa** för att
i efterhand se resväg, syfte, resdatum, sträcka, ersättning per mil samt när och av vem rapporten
lämnades in.

Körrapporten betalas ut precis som ett vanligt utlägg: bocka i den på löneposten (se ovan),
matcha den mot en bankbetalning, eller markera den som betald från utläggets sida. Utläggslistan
visar som standard bara obetalda poster – växla till **Alla** för historiken. Ersättning över
schablonen är skattepliktig och läggs i så fall som ett tillägg på lönebeskedet i stället.

## Semester

Ange **uttagna semesterdagar** på löneposten. SaldoVibe tillämpar sammalöneregeln: månadslönen
betalas som vanligt och ett **semestertillägg** på 0,43 % av månadslönen per dag läggs till som
skattepliktig lön (egen rad på lönespecifikationen). När lönekörningen avslutas dras dagarna från
den anställdes saldo, och semestertillägget bokförs som lönekostnad tillsammans med övriga tillägg.

Semesterlöneskulden bokförs inte löpande utan justeras vid bokslut, se steget
**Semesterlöneskuld** i [bokslutsflödet](01-komma-igang.md#bokslut-årsavslut).

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
4. Utlägg som valts på löneposterna flyttas från utläggsskulden (t.ex. 2820) till löneskulden
   (2910) i samma verifikation och markeras som utbetalda. Är ett valt utlägg redan utbetalt
   avbryts avslutet: *Utlägget … är redan utbetalt. Ta bort det från löneposten.*
5. Betalningspåminnelser skapas för lönerna, med nettolön plus eventuella utlägg.
6. En redan avslutad/rapporterad körning kan inte avslutas igen.

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
