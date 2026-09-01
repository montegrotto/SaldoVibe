# Integritetspolicy

Det här kapitlet beskriver vilka personuppgifter SaldoVibe behandlar om **dig som användare**,
vilka kakor (cookies) appen använder, och vilka rättigheter du har. Uppgifter om anställda,
kunder och leverantörer som bokförs i systemet ägs av företaget som använder SaldoVibe —
företaget är personuppgiftsansvarigt för dem, och SaldoVibe är verktyget.

## Vad lagras om dig som användare

- **Kontouppgifter:** e-postadress, för- och efternamn samt lösenord (lagras som hash, aldrig
  i klartext).
- **Företagskopplingar:** vilka företag ditt konto har åtkomst till och din roll.
- **Händelselogg:** systemet för en manipulationssäker logg över ändringar i bokföringen
  (krav enligt Bokföringsnämndens regler). Där registreras vem som gjort varje ändring —
  din e-postadress knyts alltså till de bokföringsposter du skapar eller ändrar. Loggen
  bevaras lika länge som bokföringen (7 år enligt bokföringslagen) och kan inte redigeras
  i efterhand.

Personnummer för anställda och e-postkontons lösenord lagras krypterade och syns aldrig i
händelseloggen.

## Kakor (cookies)

SaldoVibe använder endast två förstapartskakor, båda tekniskt nödvändiga:

| Kaka | Syfte | Livslängd |
|---|---|---|
| `sessionid` | Håller dig inloggad | Sessionen (Django-standard: 2 veckor) |
| `csrftoken` | Skyddar formulär mot förfalskade anrop (CSRF) | 1 år |

Ingen analys, ingen spårning, inga tredjepartsskript — alla script och stilar servas från
appen själv. Eftersom båda kakorna är strikt nödvändiga för tjänstens funktion krävs inget
samtyckesbanner enligt ePrivacy-reglerna.

## Dina rättigheter

Du har rätt att få ut dina uppgifter (registerutdrag), få felaktiga uppgifter rättade och få
ditt konto raderat. Kontakta företagets administratör. Observera:

- Namn och e-post kan rättas direkt under kontoinställningarna.
- Vid kontoradering anonymiseras ditt konto (e-post och namn tas bort och inloggning
  spärras). Din identitet i **historiska loggposter** kan däremot inte tas bort så länge
  bokföringslagens arkiveringskrav gäller — loggen är bokföringens behandlingshistorik och
  fryst enligt lag (dataskyddsförordningen art. 17.3 b).

## Var uppgifterna finns

Var databasen körs beror på hur företaget valt att installera SaldoVibe (egen server eller
driftad tjänst). Fråga företagets administratör vem som är personuppgiftsansvarig och var
driften sker.
