# 8. Leverantörsfakturor

## Leverantörer

**Inköp → Leverantörer**: skapa leverantörer med namn m.m. innan du registrerar fakturor (du
kan även skriva in leverantörsnamn fritt på en faktura utan att skapa ett register-objekt, men en
kopplad leverantör ger bättre spårbarhet och historik).

## Skapa en leverantörsfaktura

1. Gå till **Inköp → Leverantörsfakturor → Ny faktura**.
2. Välj leverantör, ange belopp exkl. moms per kostnadsrad (minst en rad krävs), momsbelopp och
   totalbelopp.
3. **Kostnadsrader + moms måste summera exakt till totalbeloppet** – annars avvisas formuläret med
   "Summan av kostnadsrader och moms måste vara lika med totalbelopp."
4. Bifoga underlag antingen genom att ladda upp direkt i formuläret eller via bilage-väljaren mot
   redan uppladdade [bilagor](04-bilagor.md).
5. Spara som **utkast**, eller välj **Registrera** för att spara och bokföra i samma steg.

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
  leverantörsskulden mot valt betalkonto. Delbetalningar stöds.
- **Avskrivet belopp** låter dig samtidigt skriva av en rest som inte kommer att betalas, till
  valfritt **avskrivningskonto** – t.ex. öresavrundning (3740) eller erhållen rabatt.
- Betalning och avskrivning bokförs som en verifikation; perioden för betalningsdatumet får inte
  vara låst. Betalningen kan ångras via **Ångra betalning**.

## QR-betalningsunderlag

Varje leverantörsfaktura har en genererad **QR-kod** (SVG) med betalningsuppgifter, praktisk för
att skanna vid manuell betalning i bankappen.

## Förfallopåminnelser

Leverantörsfakturor som förfaller inom 3 dagar visas som en räknare/påminnelse i klockikonen i
sidhuvudet, så att obetalda fakturor inte glöms bort.

## Nästa steg

Fortsätt till [9. Anläggningstillgångar](09-anlaggningstillgangar.md).
