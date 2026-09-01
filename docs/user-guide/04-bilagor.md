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

## Använda bilage-väljaren i andra flöden

När du registrerar t.ex. en leverantörsfaktura kan du öppna bilage-väljaren för att antingen ladda
upp en ny fil direkt i flödet, eller markera en eller flera redan uppladdade bilagor att koppla till
posten. Väljaren tar dig sedan tillbaka till formuläret du kom ifrån med bilagorna förvalda.

## E-postimport av bilagor

Om e-postimport är konfigurerat för företaget (`Inställningar → Företag`, Gmail via IMAP eller
Microsoft 365 via Microsoft Graph) hämtas bilagor automatiskt från den angivna mappen, taggas med
källa "E-post" och kopplas till avsändarens ämnesrad/meddelande-id för spårbarhet. Ett försök görs
även direkt när e-postimport aktiveras på företaget.

**Endast PDF hämtas via e-post.** Fakturamail bifogar ofta logotyper och layoutgrafik som vanliga
bilagor (inte inline), och de skulle annars fylla bilagelistan med skräp. PNG och JPEG går
fortfarande att ladda upp manuellt.

Dubbletter filtreras på filens innehåll (SHA-256), inte på meddelande-id. En vidarebefordrad
faktura räknas alltså som samma bilaga som originalet. En bilaga som tagits bort återkommer inte
vid nästa hämtning.

### När hämtningen körs

Hämtningen är schemalagd och körs **var 15:e minut** för alla aktiva företag som har
e-posthämtning påslagen. Den sköts av `scheduler`-tjänsten (ofelia), som finns i både
`docker-compose.prod.yml` och `docker-compose.yml`. Ett företag som misslyckas stoppar inte de
övriga, och kommandot avslutas med felkod så att en trasig brevlåda syns i schemaläggarloggen:

```bash
docker compose logs scheduler
```

**`docker-compose.dev.yml` har ingen scheduler** — i utvecklingsmiljön kör du kommandot manuellt.

Du kan också köra det manuellt:

```bash
python manage.py hamta_epostbilagor              # alla företag
python manage.py hamta_epostbilagor --company 2  # ett företag
```

För omedelbar återkoppling finns knappen **Hämta e-postbilagor** på företagssidan. Att slå på
e-posthämtning när ett företag skapas startar däremot ingen hämtning direkt — företagsskapandet
ska inte kunna fastna på en brevlåda som inte svarar.

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
