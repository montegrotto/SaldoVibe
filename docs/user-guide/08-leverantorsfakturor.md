---
description: "Leverantörer, registrering av leverantörsfakturor med OCR-förslag, betalning och QR-kod i SaldoVibe."
---

# 8. Leverantörsfakturor

## Leverantörer

**Inköp → Leverantörer**: skapa leverantörer med namn m.m. innan du registrerar fakturor (du
kan även skriva in leverantörsnamn fritt på en faktura utan att skapa ett register-objekt, men en
kopplad leverantör ger bättre spårbarhet och historik).

## Fakturalistan

**Inköp → Leverantörsfakturor** visar som standard endast **obetalda** fakturor. Växla till **Alla**
i listans huvud för att även se betalda fakturor.

## Utlägg

**Personal → Utlägg** visar som standard endast **obetalda** utlägg. Växla till **Alla** i listans
huvud för att även se utbetalda utlägg. Där finns även knappen **Ny körrapport**, se
[Körrapporter](06-loner.md#körrapporter).

I utläggsformuläret kan du välja en **Kategori**; kostnadskontot fylls då i med kategorins konto
och kan ändras. Ett utkast – t.ex. ett utlägg från [mobilappen](13-mobilappen.md#utlägg), där man
bara väljer kategori – ändras med **Redigera utlägg** på utläggets sida. Kontot är förifyllt med
kategorins konto; **Spara och bokför** bokför med det konto som står i formuläret. Bokförda
utlägg och körrapporter kan inte redigeras.

### Utläggskategorier

Knappen **Kategorier** i utläggslistan visar företagets kategorier, t.ex. Hotell (5831), Bränsle
(5611) och Parkering (5619). Nya företag får en standarduppsättning. Ändra namn eller konto
direkt i tabellen, lägg till en kategori i den tomma raden längst ner, bocka i **Ta bort** för
att ta bort en, och klicka **Spara**. Två kategorier kan inte ha samma namn. Utlägg som redan
har en borttagen kategori behåller sitt konto.

## Skapa en leverantörsfaktura

1. Gå till **Inköp → Leverantörsfakturor → Ny faktura**.
2. Välj leverantör, ange belopp exkl. moms per kostnadsrad (minst en rad krävs), momsbelopp och
   totalbelopp. Väljs en bilaga med tolkade fält förifylls bl.a. totalbelopp och momsbelopp.
   Redovisar företaget inte moms (momsperiod "Ingen" under
   [Företagsinställningar](11-foretagsinstallningar.md)) visas inget momsfält – hela beloppet
   bokförs som kostnad. Detsamma gäller formuläret för nya utlägg.
3. **Kostnadsrader + moms måste summera exakt till totalbeloppet** – annars avvisas formuläret med
   "Summan av kostnadsrader och moms måste vara lika med totalbelopp."
   Fakturanumret måste vara unikt per leverantör – samma nummer från en annan leverantör går bra,
   men en dubblett avvisas med "Leverantören har redan en faktura med det här fakturanumret."
4. Bifoga underlag antingen genom att ladda upp direkt i formuläret eller via bilage-väljaren mot
   redan uppladdade [bilagor](04-bilagor.md).
5. Spara som **utkast**, eller välj **Registrera** för att spara och bokföra i samma steg.

Ett utkast kan tas bort med **Ta bort utkast** i fakturans detaljvy. En registrerad (bokförd)
faktura kan inte tas bort ("Bokförda fakturor kan inte tas bort.").

## Registrera (bokföra) en faktura

Registrering bokför automatiskt:

- **Debet** kostnadskontona från kostnadsraderna.
- **Debet** ingående moms (om momsbelopp > 0).
- **Kredit** leverantörsskuld på företagets standard leverantörsskuldkonto.

En redan registrerad faktura kan inte registreras igen ("Fakturan är redan bokförd.").

## Betala en faktura

Betalningar som syns på banken bokförs normalt via bankimportens snabbbokföring, se
[5. Bank & skattekonto](05-bank-skattekonto.md). För övriga fall finns **Registrera betalning**
på fakturans detaljvy (fakturalistan länkar dit):

- Ange **betalningsdatum**, **betalt belopp** och **betalkonto** – detta bokför minskningen av
  leverantörsskulden mot valt betalkonto. Betalkontot kan vara vilket balanskonto som helst utom
  reskontrakontona: bank eller kassa, ägarens privata betalning (2893 i aktiebolag, 2018 i enskild
  firma), en anställds utlägg (2820), en kortskuld som betalas vid månadsskiftet eller ett tidigare
  bokfört förskott till leverantören (1480). Delbetalningar stöds.
- **Avvikelse** är skillnaden mellan det som regleras på fakturan och det som betalas. Ett
  positivt belopp skrivs av mot valt **avvikelsekonto** – t.ex. öresavrundning (3740), erhållen
  rabatt eller valutakursvinst (3960). Ett negativt belopp har betalats utöver fakturan:
  påminnelseavgift (6990), dröjsmålsränta (8422), bankavgift (6570) eller valutakursförlust
  (7960). Betalt belopp plus avvikelse är det som regleras på fakturan.
- Betalning och avvikelse bokförs som en verifikation; perioden för betalningsdatumet får inte
  vara låst. Betalningen kan ångras via **Ångra betalning**.

### Faktura i utländsk valuta betald med kort

Registrera fakturan i kronor med det belopp kortet drog – det syns på kortets eller bankens
kontoutdrag inom någon dag och får användas som kurs för fakturadagen (BFNAR 2013:2 punkt 2.5).
Då matchar banktransaktionen fakturan exakt. Är fakturan redan registrerad till ett annat
kronbelopp bokförs skillnaden som avvikelse: valutakursförlust (7960, negativ avvikelse) om kortet
drog mer, valutakursvinst (3960, positiv avvikelse) om det drog mindre – eller som en extrarad när
transaktionen bokförs i Bank-vyn.

## QR-betalningsunderlag

Varje leverantörsfaktura har en genererad **QR-kod** (SVG) med betalningsuppgifter, praktisk för
att skanna vid manuell betalning i bankappen.

## Förfallopåminnelser

Leverantörsfakturor som förfaller inom 3 dagar visas som en räknare/påminnelse i klockikonen i
sidhuvudet, så att obetalda fakturor inte glöms bort.

## Nästa steg

Fortsätt till [9. Anläggningstillgångar](09-anlaggningstillgangar.md).
