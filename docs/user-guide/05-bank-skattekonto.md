---
description: "Bankkällor, import av kontoutdrag och snabbbokföring av bank- och skattekontohändelser i SaldoVibe."
---

# 5. Bank & skattekonto

## Lägg upp en bankkälla

En bankkälla kopplar ett faktiskt konto (bank, sparkonto, kreditkort eller skattekonto) till ett
bokföringskonto i kontoplanen.

1. Gå till **Inställningar → Bank- & skattekällor**.
2. Klicka **Ny bankkälla**, ange namn, typ (Bankkonto/Skattekonto/Sparkonto/Kreditkort) och vilket
   bokföringskonto den ska bokas mot.
3. Ett **skattekonto måste kopplas till bokföringskonto 1630** – annars sparas det inte. Det får
   också bara finnas **ett** skattekonto per företag.
4. Namnet måste vara unikt per företag.
5. Ett skattekonto kan inte redigeras via det vanliga redigeringsformuläret ("Skattekonto hanteras
   automatiskt och kan inte redigeras här.") – det hanteras av skattekonto-importflödet.
6. Välj gärna ett standardformat (CSV-profil) per källa, t.ex. Swedbank, Nordea, SEB,
   Handelsbanken, Danske Bank, Skatteverket m.fl. – annars försöker systemet auto-detektera formatet.
   Profiler markerade **(otestad)** är byggda efter bankens dokumenterade exportformat men har
   inte verifierats mot en riktig exportfil – fungerar importen inte, prova profilen
   **Generisk CSV** eller hör av dig.

## Importera kontoutdrag

1. Gå till **Bokföring → Bank & skattekonto**.
2. Välj bankkälla, klicka på **Välj CSV-fil och importera** och välj filen med kontoutdrag –
   importen startar direkt när filen är vald.
3. Skattekonton tolkas alltid med Skatteverkets skattekonto-format; övriga konton använder källans
   valda profil (eller auto-detektering).
4. Rader som redan importerats tidigare (samma externa transaktions-id per konto) läggs inte till
   igen – importen rapporterar bara hur många **nya** transaktioner som lades till.

Du kan även registrera en enskild transaktion manuellt istället för att importera en fil.

## Bokföra banktransaktioner

Listan visar som standard endast **ohanterade** (obokförda) transaktioner för vald bankkälla.
Växla till **Alla** i listans huvud för att även se bokförda transaktioner.

Varje importerad rad bokförs på ett av två sätt:

- **Snabbbokföring** – systemet föreslår en motpart automatiskt baserat på tidigare mönster eller
  en matchande kund-/leverantörsfaktura (belopp + datum). Ett klick bokför transaktionen enligt
  förslaget. Är transaktionen redan bokförd, eller finns inget förslag, avvisas åtgärden med ett
  tydligt meddelande.
- **Manuell bokföring** – öppna transaktionen och välj konteringsrader själv, t.ex. när ingen
  automatisk matchning finns.

Matchar en banktransaktion en obetald kund- eller leverantörsfaktura markeras fakturan automatiskt
som betald när bokföringen sker via snabbbokföring eller manuell bokföring med fakturakoppling.

## Ta bort en banktransaktion

Endast **obokförda** banktransaktioner kan tas bort. Försöker du ta bort en redan bokförd
transaktion får du meddelandet "Bokförda banktransaktioner kan inte tas bort." – då krävs istället
en korrigeringsverifikation på den bokförda posten (se [Löpande bokföring](02-lopande-bokforing.md)).

## Följa upp skattekontot

Startsidan har en ruta **Skattekonto** som ställer bokföringen mot Skatteverket:

- **Bokfört saldo (1630)** – saldot i huvudboken.
- **Saldo enligt Skatteverket** – saldot på den senast importerade skattekontoraden, med datum.
  Står det "Inget importerat" har skattekontot aldrig importerats.
- **Differens** – bokfört minus Skatteverkets saldo. Skilt från noll betyder normalt att nyare
  rader inte importerats eller att importerade rader ännu inte bokförts.
- **Nästa dragning** – datum och totalbelopp för nästa förväntade dragning inom 30 dagar, med
  raderna under: **Moms** (obetald moms på 2650), **Avdragen skatt** (2710),
  **Arbetsgivaravgift** (2731) och **Debiterad preliminärskatt** (beloppet som angetts under
  [Företagsinställningar](11-foretagsinstallningar.md#redigera-företagsuppgifter)). Samma
  uppdelning som Skatteverkets "Kommande transaktioner".

Täcker det bokförda saldot inte dragningarna de närmaste 30 dagarna visas en varning i rutan,
"Saldot täcker inte dragningen …", och notisklockan får posten **Skattekontot täcker inte nästa
dragning**. Har det gått mer än 35 dagar sedan senaste skattekontoimport får klockan posten
**Skattekontot behöver importeras** – den visas bara för företag som importerat skattekontot minst
en gång. Båda ingår i den dagliga notisdigesten.

## Nästa steg

Fortsätt till [6. Löner](06-loner.md).
