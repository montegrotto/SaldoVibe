# 9. Anläggningstillgångar

## Tillgångstyper

**Bokföring → Anläggningstillgångar → Typer**: en typ (t.ex. "Datorer och IT-utrustning",
"Maskiner", "Byggnader" osv.) definierar vilka konton som används för alla tillgångar av den
typen: **avskrivningskonto** (debet) och **ackumulerat avskrivningskonto** (kredit), motsvarande
par för nedskrivningar, samt **tillgångskonto** (t.ex. 1220) och **vinst-/förlustkonto vid
avyttring** (t.ex. 3973/7973) som används när tillgången avyttras eller utrangeras.
Standardtyperna får BAS-konton föreslagna automatiskt. Sätt upp typerna innan du lägger till
tillgångar.

## Registrera en tillgång

1. Gå till **Bokföring → Anläggningstillgångar → Ny tillgång**.
2. Ange namn, typ, inköpsdatum, startdatum för avskrivning, **anskaffningsvärde**, **restvärde**
   och **nyttjandeperiod i månader**.
3. Valideringsregler:
   - Anskaffningsvärdet måste vara större än 0.
   - Restvärdet kan inte vara negativt eller högre än anskaffningsvärdet.
   - Nyttjandeperioden måste vara minst 1 månad.
   - När avskrivning väl har påbörjats kan restvärdet inte sättas lika med anskaffningsvärdet
     (det skulle innebära noll avskrivning på en tillgång som redan skrivs av).
4. Den månatliga avskrivningen beräknas automatiskt som
   `(anskaffningsvärde − restvärde) / nyttjandeperiod (månader)`.

## Köra månadsavskrivning

Från tillgångens rad/detaljvy, klicka **Avskriv**:

- En avskrivningspost skapas för nästa period i ordningen (en per kalendermånad, aldrig två för
  samma period på samma tillgång).
- En bokföringsverifikation skapas automatiskt: debet tillgångstypens avskrivningskonto, kredit
  ackumulerat avskrivningskonto.
- Är tillgången redan fullt avskriven (avskrivet belopp når det avskrivningsbara beloppet, eller
  antalet avskrivningar når nyttjandeperioden) avvisas ytterligare avskrivning – kontrollera då att
  anskaffningsvärdet verkligen är högre än restvärdet.
- Faller bokföringsdatumet i en låst period avvisas bokföringen ("Perioden för bokföringsdatumet
  är låst. Bokför i öppen period eller lås upp perioden."). Detsamma gäller nedskrivningar och
  korrigeringar av tidigare poster vars period har hunnit låsas.

## Påminnelser

Tillgångar som är redo för nästa månads avskrivning visas som en räknare i klockikonen i
sidhuvudet, så att avskrivningskörningar inte glöms bort. Påminnelsen kan kvitteras utan att
avskrivningen körs.

## Avyttra eller utrangera en tillgång

Från tillgångens detaljvy, **Avyttra/utrangera**: ange datum, typ av avgång (såld, utrangerad,
övrigt), anledning och – vid försäljning – **försäljningspris exkl. moms** och vilket konto det
bokförs mot (förvalt 1930). En balanserad verifikation skapas automatiskt: tillgångskontot
krediteras med anskaffningsvärdet, ackumulerade av- och nedskrivningar debiteras, försäljningspriset
debiteras valt konto och skillnaden mot bokfört värde hamnar på typens vinst- eller förlustkonto.
Verifikationen länkas från tillgången. Avgången avvisas om datumet ligger i en låst period eller om
tillgångstypen saknar något av kontona.

## Ta bort en tillgång

Borttagning av en anläggningstillgång är en **permanent radering**, till skillnad från
[bilagor](04-bilagor.md) som mjuk-raderas. Radera med eftertanke, särskilt om tillgången redan har
avskrivningsposter kopplade till bokföringen.

## Nästa steg

Fortsätt till [10. Rapporter](10-rapporter.md).
