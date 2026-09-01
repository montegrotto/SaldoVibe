# 1. Komma igång

## Registrera konto och logga in

SaldoVibe använder e-postadress som inloggnings-id, inte användarnamn.

1. Gå till **Registrera** (`/konto/register/`).
2. Fyll i e-postadress, förnamn, efternamn och lösenord (bekräfta lösenordet en gång till).
3. Efter registrering loggas du in automatiskt och skickas till **Översikt**.

Nästa gång loggar du in via **Logga in** med e-post och lösenord.

## Skapa ditt första företag

Innan du kan bokföra något behöver du ett företag. Ett konto kan ha flera företag och du växlar
mellan dem via **Företag**-väljaren längst upp till höger.

1. Gå till **Inställningar → Företag** i vänstermenyn.
2. Klicka **Nytt företag**.
3. Fyll i minst namn och organisationsnummer. Övriga fält (adress, bankgiro/plusgiro,
   momsperiod, momsstartdatum, e-postimport av bilagor) kan kompletteras senare under
   **Inställningar → Företag → redigera**.
4. Spara.

När företaget skapas händer automatiskt tre saker:

- **BAS 2026-kontoplanen** laddas in i sin helhet för företaget (du behöver alltså inte skapa
  konton manuellt för att komma igång).
- Företaget väljs som **aktivt företag** för dig.
- Om du har fyllt i e-postimport-uppgifter för bilagor försöker systemet hämta bilagor direkt
  (går det fel blockerar det inte att företaget skapas – du får ett varningsmeddelande istället).

Om kontoplanen av någon anledning inte går att ladda in avbryts hela företagsskapandet och du får
ett felmeddelande – prova då igen eller kontrollera att BAS-dataunderlaget i installationen är intakt.

### Byta aktivt företag

Använd **Företag**-väljaren i den övre raden (syns bara om du har fler än ett företag). Allt du gör i
appen – verifikationer, fakturor, rapporter – gäller alltid det just då aktiva företaget.

## Kontoplan

Under **Inställningar → Kontoplan** ser du alla konton som seedades från BAS 2026. Du kan:

- Redigera namn, kontoklass, momsfältkod, SRU-kod, beskrivning och om kontot är aktivt.
- **Kontonumret går inte att ändra** efter att kontot skapats – det är låst i formuläret.
- Avaktivera konton du inte använder (`Aktivt`-kryssrutan) istället för att ta bort dem; endast
  aktiva konton går att välja på nya verifikationsrader.

## Räkenskapsår

Innan du kan registrera verifikationer eller importera SIE-filer måste minst ett räkenskapsår finnas.

1. Gå till **Inställningar → Räkenskapsår**.
2. Klicka **Nytt räkenskapsår**.
3. För det första räkenskapsåret väljer du själv start- och slutdatum.
4. För efterföljande år föreslår och **låser** systemet startdatumet till dagen efter föregående
   räkenskapsårs slutdatum – räkenskapsår måste följa varandra utan glapp eller överlapp.
   Slutdatum föreslås automatiskt till ett år minus en dag efter startdatumet, men kan ändras.

Ett skapat räkenskapsår **går inte att redigera** i efterhand (försök att öppna redigeringsvyn ger
meddelandet "Ett skapat räkenskapsår kan inte ändras. Du kan endast ta bort det."). Vill du ändra
datumen måste du ta bort räkenskapsåret och skapa ett nytt – vilket bara går om det inte har några
kopplade verifikationer, se nästa kapitel.

Knappen **Budget** på räkenskapsårets rad öppnar resultatbudgeten för det året, se
[Resultatbudget](10-rapporter.md#resultatbudget) i rapportkapitlet. Knappen **Bokslut** öppnar
bokslutsflödet, se nästa avsnitt.

## Bokslut (årsavslut)

Knappen **Bokslut** på räkenskapsårets rad öppnar en guidad sida som avslutar året. Flödet kräver
rollen `finance_admin` och att **Bolagsform** är satt i
[företagsinställningarna](11-foretagsinstallningar.md#redigera-företagsuppgifter). Sidan visar fem
steg:

1. **Förkontroller** – alla måste vara gröna innan bokslutet kan bokföras:
   - Bolagsform är satt (Aktiebolag eller Enskild firma).
   - Tidigare räkenskapsår är avslutade eller saknar poster – år stängs i kronologisk ordning.
   - Alla perioder utom årets sista är låsta (se
     [Periodlåsning](02-lopande-bokforing.md)), men årets sista dag får **inte** vara låst –
     bokslutsverifikationen dateras där. Helårslåsningen görs som sista steg.
   - Balanskontrollen: balansräkningens differens ska vara exakt årets resultat.
2. **Nästa räkenskapsår** – skapas med ett klick om det saknas (omföringen bokförs på dess
   första dag).
3. **Bokslutsverifikationer** – två verifikationer bokförs i serie S:
   - **S1** (årets sista dag): årets resultat förs från konto 8999 till 2099 (aktiebolag)
     respektive 2019 (enskild firma).
   - **S2** (nästa års första dag): för aktiebolag förs beloppet om från 2099 till 2091
     ("balanseras i ny räkning"); för enskild firma nollställs kontona 2011–2019 (de med saldo)
     mot 2010.
4. **Ingående balans** – en bekräftelsevy med nästa års beräknade ingående saldon per konto.
   Årets resultat står kvar på 2099/2019 i ingående balans; omföringen (S2) är daterad på nästa
   års första dag och syns i nästa års saldon.
5. **Helårslåsning** – hela året låses så att inga poster kan ändras. Detta är sista steget.

Formell vinstdisposition för aktiebolag (utdelning, avsättning till reservfond) ingår inte i
flödet – S2 balanserar alltid hela beloppet i ny räkning; vill du dela upp dispositionen gör du
det med en vanlig manuell verifikation i det nya året. Flödet omfattar aktiebolag och enskild
firma med en ägare; handelsbolag/kommanditbolag stöds inte.

**Ångra ett bokslut:** det finns ingen egen ångra-funktion. En `finance_admin` låser upp
perioden under **Inställningar → Periodlåsning** och skapar korrigeringar av S1/S2 via
**Korrigera** på respektive verifikation – därefter kan bokslutsflödet köras igen för året.

## Nästa steg

Fortsätt till [2. Löpande bokföring](02-lopande-bokforing.md) för att registrera din första
verifikation.
