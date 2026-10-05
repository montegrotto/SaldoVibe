---
description: "SaldoVibe-appen för iPhone: logga in med QR-kod eller lösenord, fotografera kvitton, registrera utlägg och leverantörsfakturor, se läget, läsa resultat- och balansräkningen och markera fakturor som betalda."
---

# 13. Mobilappen

Appen är till för det man gör på språng: fotografera kvitton och fakturor, registrera utlägg
och leverantörsfakturor direkt från bilden, se läget i företaget, läsa resultat- och
balansräkningen och markera fakturor som betalda. Allt annat – verifikationer, övriga rapporter,
moms, lön, SIE, bank och inställningar – görs på datorn.

Appen finns för iPhone (iOS 18 eller senare). Den finns inte i App Store utan byggs från
källkoden i mappen `ios/` med Xcode och installeras på telefonen via Xcode eller TestFlight;
stegen står i `ios/README.md`.

## Logga in

Gå till **Mobilappen** längst ner i menyn på datorn. Där finns två sätt:

- **QR-kod:** klicka på **Visa QR-kod för inloggning**. Öppna appen, tryck **Skanna QR-kod** och
  rikta kameran mot skärmen. Koden gäller i 15 minuter och visas bara en gång – behöver du en ny
  klickar du på knappen igen. Läser du sidan på telefonen trycker du i stället på **Öppna i appen**.
- **Lösenord:** skriv in serveradressen som visas på sidan (samma adress som i webbläsaren), din
  e-postadress och ditt lösenord i appen.

Appen får en egen inloggning, skild från webbläsarens. Under **Inloggade enheter** ser du varje
app-inloggning, när den skapades och senast användes, och kan **återkalla** den – appen loggas då
ut direkt. **Logga ut** i appen (fliken **Mer**) tar bort inloggningen på servern. En QR-kod som
visats men aldrig skannats förfaller av sig själv.

Har du flera företag väljer du vilket under **Mer**; alla flikar visar det valda företaget. Med
läsbehörighet (se [Företagsinställningar](11-foretagsinstallningar.md)) kan du se allt men inte
skapa, bokföra eller registrera betalningar – appen svarar då "Du har bara läsbehörighet i det
här företaget."

## Översikt

Första fliken visar kassa/bank (samma konton som likviditetsprognosen på startsidan), årets
intäkter, kostnader och resultat, summan av obetalda leverantörs- och kundfakturor, samt samma
påminnelser som klockan uppe till höger på webben – förfallna fakturor, momsdeklaration att
lämna, löneutbetalningar och så vidare. Dra nedåt för att uppdatera.

**Resultaträkning** och **Balansräkning** i samma flik öppnar rapporterna som de ser ut under
**Rapporter** på webben (se [Rapporter](10-rapporter.md#balansräkning-och-resultaträkning)):
samma sektioner, konton och summor. Räkenskapsår väljs med kalenderknappen uppe till höger; i
resultaträkningen kan perioden avgränsas till valfria månader (**Från**/**Till**). Balansräkningen
visar ställningen per räkenskapsårets sista dag, och **Beräknat resultat** är skillnaden mellan
tillgångar och eget kapital plus skulder – årets resultat innan det förts över till eget kapital.
Budgetkolumner, verifikationsrader per konto och PDF-export finns bara på webben.

## Kvitton

Fliken **Kvitton** visar bilagor som ännu inte kopplats till något, alltså samma lista som
**Bokföring → Bilagor** på webben.

- **Skanna** öppnar kameran med iOS egen dokumentskanner: den hittar kvittots kanter, rätar upp
  bilden och tar flera sidor i följd. En sida sparas som JPEG, flera sidor som en PDF.
- **Välj foto** tar en bild ur bildbiblioteket.

Filen laddas upp direkt och går genom samma fältigenkänning som en uppladdning på webben.
Förslagen (datum, totalbelopp, moms, leverantör, fakturanummer, förfallodatum) fylls i när du
registrerar något från bilagan – de är bara förslag och kan ändras innan du sparar.

Tryck på en bilaga för att se den i full storlek. Därifrån: **Registrera som utlägg** eller
**Registrera som leverantörsfaktura**.

## Utlägg

Samma uppgifter som på webben: beskrivning, datum, totalbelopp, moms (bara om företaget är
momsregistrerat), kostnadskonto och anställd eller namn. Kvittot kan skannas direkt i formuläret
eller väljas bland de uppladdade bilagorna. **Spara utkast** lägger utlägget i listan utan att
bokföra; **Bokför** registrerar det direkt med samma konton och kontroller som på webben
(skuldkonto 2820/2890, ingående moms 2640, låsta perioder).

Listan visar obetalda utlägg; **Visa alla** tar med utbetalda. Ett bokfört utlägg betalas ut med
**Registrera betalning**: datum, belopp och betalkonto (förvalt 1930).

## Leverantörsfakturor

Fotografera fakturan eller välj en uppladdad bilaga. Leverantör, fakturanummer, OCR, fakturadatum,
förfallodatum, totalbelopp och moms föreslås från bilden. Hittas inte leverantören i
leverantörsregistret erbjuder appen att skapa den med namnet från fakturan.

Hela beloppet exklusive moms bokförs på **ett** kostnadskonto. Ska kostnaden delas på flera
konton registreras fakturan på datorn, där kostnadsrader kan läggas till.

Listan visar obetalda fakturor med förfallna markerade i rött; **Visa alla** tar med betalda.
Betalning registreras med datum, belopp och betalkonto precis som på webben – ett belopp som
avviker upp till 1 kr skrivs av som öresavrundning.

## Kundfakturor

Obetalda kundfakturor med förfallna markerade, detaljer med fakturarader och bilagor, och
**Registrera betalning** när pengarna kommit in. Kundfakturor skapas, skickas och krediteras på
datorn.

## Felsökning

| Meddelande i appen | Betydelse |
| --- | --- |
| "Fel e-postadress eller lösenord." | Samma kontroll som webbinloggningen. |
| "Ogiltig eller återkallad inloggning." | Inloggningen är återkallad under **Mobilappen**, användaren är avaktiverad, eller QR-koden hann gå ut innan den skannades. Logga in på nytt. |
| "Du har inte tillgång till företaget." | Du har tagits bort från företaget. Välj ett annat under **Mer**. |
| "Du har bara läsbehörighet i det här företaget." | Rollen **Endast läsa** – se [Företagsinställningar](11-foretagsinstallningar.md). |
| "Perioden … är låst" | Datumet ligger i en låst period; se [Löpande bokföring](02-lopande-bokforing.md). |
| "iOS tillåter bara krypterade anslutningar (https) …" | Adressen pekar på ett domännamn över `http://`. iOS släpper inte fram okrypterad trafik till domännamn; använd `https://`, eller serverns IP-adress när du är på det lokala nätverket. Säger QR-koden `http://` fast sajten är https, sätt `SALDOVIBE_PUBLIC_URL` på servern (appen provar ändå https först). |
| Kan inte nå servern | Kontrollera serveradressen (den som visas under **Mobilappen**) och att telefonen når den – en server som bara finns på det lokala nätverket kräver att du är på samma nätverk eller VPN. |
