---
description: "Bilagor och underlag i SaldoVibe: uppladdning, delning från mobilen, e-postimport, mjuk radering och legal hold enligt Bokföringslagens arkiveringskrav."
---

# 4. Bilagor

Bilagor (underlag) hanteras samlat under **Bokföring → Bilagor**, och kan även bifogas
direkt från flöden som leverantörsfakturaregistrering via en bilage-väljare.

## Ladda upp en bilaga

1. Gå till **Bokföring → Bilagor**.
2. Klicka på **Ladda upp fil** och välj fil – uppladdningen startar direkt när filen är vald.
   Endast **PDF, PNG och JPEG** accepteras – andra filtyper avvisas med
   "Endast PDF, PNG eller JPEG är tillåtet."
3. En miniatyrbild genereras automatiskt (för PDF visas en platshållarbild med filnamn om
   sidrendering inte är möjlig).

## Ladda upp från mobilen (dela-knappen)

Har du en iPhone är [appen](13-mobilappen.md) det enklaste sättet: den fotograferar kvittot
med iOS egen dokumentskanner och laddar upp det direkt. Genvägen nedan behövs bara om du vill
dela filer från andra appar eller använder Android.

Från mobilen kan du skicka filen du tittar på – en PDF i Filer eller Mail, eller ett kvitto du just
skannat – direkt till bilagelistan via dela-knappen. Det bygger på en personlig
**uppladdningstoken** och en genväg som du sätter upp en gång:

- **iPhone och iPad:** en egen genväg i den inbyggda appen **Genvägar**; ingen separat app behövs.
- **Android:** appen **HTTP Shortcuts**, eftersom Android saknar en inbyggd motsvarighet.

### 1. Skapa en token

1. Gå till **Bokföring → Bilagor** och fäll ut **Ladda upp från mobilen (dela-knappen)**.
2. Klicka på **Skapa token**. Sidan visar **Adress** och **Token**. Kopiera båda direkt –
   token visas bara den här gången (SaldoVibe sparar bara ett kontrollvärde av den).

En token är personlig och **låst till det företag som var aktivt när den skapades** – filer som
skickas med den hamnar alltid där, oavsett vilket företag du har valt i webbläsaren. Du har högst
en token per företag: **Skapa ny token (ersätter den gamla)** gör den tidigare ogiltig, och
**Återkalla** tar bort den helt. Token slutar också fungera om du förlorar åtkomsten till
företaget eller bara har läsbehörighet där. Uppladdade filer står med dig som uppladdare.

### 2. iPhone och iPad: bygg genvägen (en gång per företag)

Menynamnen nedan är de svenska i iOS, med de engelska inom parentes.

1. Öppna **Genvägar** (Shortcuts), tryck **+** och ge genvägen ett namn, t.ex. "Till SaldoVibe".
2. Öppna genvägens informationsruta (ⓘ) och slå på **Visa i delningsblad** (Show in Share Sheet).
   Begränsa gärna det som tas emot till **PDF:er**, **bilder** och **filer**.
3. Lägg till åtgärden **Hämta innehåll från URL** (Get Contents of URL) och ställ in:
    - **URL**: adressen från bilagesidan, exakt som den står. Den **måste sluta med `/`**
      (`…/bilagor/api/ladda-upp/`) – utan snedstrecket omdirigeras anropet och filen tappas på vägen.
    - **Metod** (Method): `POST`
    - **Rubriker** (Headers): nyckel `Authorization`, värde `Bearer ` följt av din token
    - **Begärandetext** (Request Body): **Formulär** (Form), med ett fält av typen **Fil** (File)
      som har **Genvägsindata** (Shortcut Input) som värde. Fältets namn spelar ingen roll, men
      typen måste vara Fil, inte Text. Servern tar också emot filen som själva begärandetexten,
      dvs. **Fil** i stället för Formulär.
4. Lägg till **Visa resultat** (Show Result) med resultatet från föregående steg, så ser du
   serverns svar direkt på skärmen.

Överst i genvägen ska det nu stå **Få … från delningsblad**. Testa första gången från appen
**Filer**: håll fingret på en PDF, välj **Dela** och sedan genvägen. Delar du från Safari kan
genvägen få sidans adress i stället för själva filen.

Därefter: tryck på dela-knappen där du ser filen och välj genvägen. Filen dyker upp i bilagelistan
precis som en vanlig uppladdning, med miniatyr och eventuella föreslagna fält. Skickas filen som
själva begärandetexten följer filnamnet inte med; bilagan heter då `delad-<datum>-<tid>` med
filändelse efter innehållet.

Syns genvägen inte i delningsbladet: kontrollera att den tar emot filtypen, slå av och på
**Visa i delningsblad** och starta om enheten. Du kan också ställa in **Om det inte finns någon
indata** på **Fråga efter → Filer** och starta genvägen direkt; då väljer du filen i en filväljare.

### Skanna kvitton på iPhone

Den inbyggda skannern i appen **Filer** rätar upp bilden och sparar en PDF som går att dela direkt:

1. Öppna **Filer** och gå till mappen där du vill spara, t.ex. "På min iPhone".
2. Tryck på **⋯** och välj **Skanna dokument**.
3. Håll telefonen över kvittot. Bilden tas automatiskt när kanterna hittas; annars trycker du på
   avtryckaren och drar hörnen rätt. Fler sidor hamnar i samma PDF.
4. Tryck **Spara**, håll sedan fingret på PDF:en och välj **Dela** och genvägen.

Lägg kvittot plant mot ett underlag med kontrast, använd jämnt ljus utan blixt och välj gärna
filtret **Gråskala** eller **Svartvitt** – det ger mindre filer och tydligare text. Skannern i
**Anteckningar** fungerar likadant, men skanningen hamnar då i en anteckning som först får delas
som PDF.

Foton tagna med kameran är som standard i HEIC-format, som inte accepteras – lägg i så fall till
åtgärden **Konvertera bild** (Convert Image) till JPEG före uppladdningssteget.

### 3. Android: appen HTTP Shortcuts

Uppladdningen är densamma oavsett telefon, men på Android behövs en app som kan ta emot en delad
fil och skicka den vidare. [HTTP Shortcuts](https://http-shortcuts.rmy.ch/) är gratis, har öppen
källkod (MIT) och finns på Google Play och F-Droid.

**Stegen nedan följer appens egen dokumentation men är inte provade mot SaldoVibe på en riktig
Android-telefon.** Namnen är de i appens engelska gränssnitt.

1. Skapa en token enligt steg 1 ovan.
2. Skapa en ny genväg i HTTP Shortcuts och ställ in:
    - **Method**: `POST`
    - **URL**: adressen från bilagesidan, med avslutande `/`
    - **Request Headers**: `Authorization` med värdet `Bearer ` följt av din token
    - **Request Body Type**: **Parameters (form-data)**, med en parameter av typen **Single File**.
      Alternativt **File (Picker)**, som skickar filen som själva begärandetexten.
3. Spara genvägen. Dela sedan filen från valfri app via **Send to…** och välj HTTP Shortcuts.
4. På Android 11 och senare: kryssa i **Direct Share target** under **Trigger & Execution
   Settings**, så syns genvägen direkt i delningsmenyn. Har du flera genvägar som tar emot filer
   får du annars välja vilken som ska användas.

För att skanna kvitton till PDF på Android fungerar t.ex. skannern i Google Drive-appen.

### Svar och felsökning

Samma regler som för vanlig uppladdning gäller: PDF, PNG eller JPEG, högst 25 MB.

| Svar | Betydelse |
| --- | --- |
| `201` med `{"id": …, "file_name": …}` | Filen är uppladdad |
| `401` "Ogiltig eller återkallad token." | Token saknas, är fel, ersatt eller återkallad – eller så har du inte längre skrivbehörighet i företaget |
| `400` "Ingen fil togs emot. …" | Anropet innehöll ingen PDF, PNG eller JPEG. Meddelandet avslutas med vad som faktiskt kom in (innehållstyp och namnen på eventuella textfält) – ett textfält där betyder att formulärfältet har typen Text i stället för Fil |
| `400` med annat felmeddelande | Filen har fel filtyp eller är för stor |
| `405` utan meddelande | Adressen saknar det avslutande `/` – anropet omdirigerades och filen följde inte med |

Vilken klient som helst som kan skicka ett formulär fungerar, till exempel:

```bash
curl -H "Authorization: Bearer <token>" -F file=@kvitto.pdf https://saldovibe.example.se/bilagor/api/ladda-upp/
```

## Använda bilage-väljaren i andra flöden

När du registrerar t.ex. en leverantörsfaktura kan du öppna bilage-väljaren för att antingen ladda
upp en ny fil direkt i flödet, eller markera en eller flera redan uppladdade bilagor att koppla till
posten. Väljaren tar dig sedan tillbaka till formuläret du kom ifrån med bilagorna förvalda.

## E-postimport av bilagor

Om e-postimport är konfigurerat för företaget (`Inställningar → Företag`: Gmail eller en annan
IMAP-server, eller Microsoft 365 via Microsoft Graph) hämtas bilagor automatiskt från den angivna
mappen, taggas med källa "E-post" och kopplas till avsändarens ämnesrad/meddelande-id för
spårbarhet. Ett försök görs även direkt när e-postimport aktiveras på företaget.

**Endast PDF hämtas via e-post.** Fakturamail bifogar ofta logotyper och layoutgrafik som vanliga
bilagor (inte inline), och de skulle annars fylla bilagelistan med skräp. PNG och JPEG går
fortfarande att ladda upp manuellt.

Dubbletter filtreras på filens innehåll (SHA-256), inte på meddelande-id. En vidarebefordrad
faktura räknas alltså som samma bilaga som originalet. En bilaga som tagits bort återkommer inte
vid nästa hämtning.

### När hämtningen körs

Hämtningen är schemalagd och körs **var 15:e minut** för alla aktiva företag som har
e-posthämtning påslagen. Den sköts av `scheduler`-tjänsten (ofelia) i
`docker-compose.yml`. Ett företag som misslyckas stoppar inte de
övriga, och kommandot avslutas med felkod så att en trasig brevlåda syns i schemaläggarloggen:

```bash
docker compose logs scheduler
```

I utvecklingsmiljön (bar `manage.py runserver`) finns ingen scheduler — där, eller för en
engångskörning, kör du kommandot manuellt:

```bash
python manage.py hamta_epostbilagor              # alla företag
python manage.py hamta_epostbilagor --company 2  # ett företag
```

För omedelbar återkoppling finns knappen **Hämta e-postbilagor** på företagssidan. Att slå på
e-posthämtning när ett företag skapas startar däremot ingen hämtning direkt — företagsskapandet
ska inte kunna fastna på en brevlåda som inte svarar.

### Sätta upp Gmail eller en annan IMAP-brevlåda

Välj **Gmail** eller **Annan IMAP-server** som leverantör och ange e-postkontot och dess lösenord.
Gmail har fast server (`imap.gmail.com`) och kräver ett app-lösenord. För en annan IMAP-server
anger du dessutom **IMAP-server**, **IMAP-port** (993 hos de flesta leverantörer; anslutningen
görs alltid över TLS) och, bara om det skiljer sig från e-postadressen, **IMAP-användarnamn**.
Ett sparat lösenord behålls när fältet lämnas tomt vid nästa redigering. **Inkorgsmapp** är den
mapp som läses av, med serverns eget namn (oftast `INBOX`).

### Sätta upp Microsoft 365 (Exchange Online)

SaldoVibe läser brevlådan med en egen appregistrering och **utan att någon loggar in** — ingen
MFA-anslutning behövs och inget som går ut efter 90 dagar. Appen begränsas till en enda brevlåda.

Stegen görs av kundens Microsoft 365-administratör, en gång per företag.

**1. Registrera appen** i [entra.microsoft.com](https://entra.microsoft.com) → *App registrations*
→ *New registration*. Välj **Single tenant**, lämna Redirect URI tom. Notera från *Overview*:

- `Application (client) ID` — obs, **inte** `Object ID` på samma sida
- `Directory (tenant) ID`

**2. Skapa en Client Secret** under *Certificates & secrets*. Värdet visas bara en gång. Notera
utgångsdatumet — importen slutar fungera när hemligheten går ut.

**3. Ge åtkomst till en enda brevlåda.** Lägg **inte** till `Mail.Read` under *API permissions* —
det ger appen läsrätt till varje brevlåda i tenanten. Använd i stället RBAC for Applications i
Exchange Online, som begränsar appen till bokföringsbrevlådan:

```powershell
Connect-ExchangeOnline
New-ServicePrincipal -AppId <client-id> -ObjectId <objectid> -DisplayName "SaldoVibe"
New-ManagementScope -Name "SaldoVibe bilagor" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'faktura@kunden.se'"
New-ManagementRoleAssignment -App <objectid> -Role "Application Mail.Read" -CustomResourceScope "SaldoVibe bilagor"
Test-ServicePrincipalAuthorization -Identity <objectid> -Resource faktura@kunden.se | Format-Table
```

`<objectid>` hämtas från *Enterprise applications* → appen → *Overview* → `Object ID`. Det är ett
annat värde än `Object ID` under *App registrations*, och förväxlingen ger ett felmeddelande som
inte avslöjar vad som är fel.

Sista kommandot ska visa `Application Mail.Read` med `InScope: True`. Kör det mot en annan
brevlåda också och bekräfta `False` — det är beviset på att begränsningen håller.

**4. Fyll i Tenant ID, Client ID och Client Secret** under `Inställningar → Företag`, välj Outlook
som leverantör och ange brevlådans adress samt mapp.

### Felsökning

| Symptom | Trolig orsak |
| --- | --- |
| `AADSTS700016` | Client ID är ett Object ID, inte Application (client) ID |
| `AADSTS7000222` | Client Secret har gått ut — skapa en ny i Entra |
| 403 vid hämtning | RBAC-tilldelningen saknas, eller behörighetscachen har inte uppdaterats (upp till 2 h) |
| "Hittade ingen mapp med namnet ..." | Mappnamnet matchar ingen mapp i brevlådan; felmeddelandet listar tillgängliga mappar |
| `New-ManagementRoleAssignment` nekas | Kontot saknar delegering; måste vara medlem i Organization Management |

## Ta bort en bilaga

Bilagor tas **aldrig bort permanent** via den vanliga borttagningsknappen:

- Borttagning är en **mjuk radering** – bilagan markeras med borttagningstidpunkt, vem som tog bort
  den, och en valfri orsakstext, men filen och posten finns kvar i databasen.
- Är bilagan markerad med **rättsligt bevarandekrav** (legal hold) går den inte att ta bort alls –
  du får meddelandet "Bilagan är låst för bevarande och kan inte tas bort."
- Redan borttagna bilagor visas inte i den vanliga listan, och ett nytt borttagningsförsök ger bara
  "Bilagan är redan borttagen."

Detta är medvetet – underlag är en del av bokföringens revisionsspår och ska kunna återskapas vid
en revision, se `docs/compliance/`.

## Nästa steg

Fortsätt till [5. Bank & skattekonto](05-bank-skattekonto.md).
