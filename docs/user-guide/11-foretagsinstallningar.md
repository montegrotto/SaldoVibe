# 11. Företagsinställningar

## Redigera företagsuppgifter

**Inställningar → Företag → redigera** samlar allt du kan konfigurera efter att företaget skapats:

- Grunduppgifter: namn, organisationsnummer, adress, telefon, e-post, bankgiro/plusgiro, logotyp.
- **Bolagsform** (Aktiebolag eller Enskild firma) – krävs för bokslutsflödet, som vägrar starta
  tills fältet är satt. Styr hur årets resultat bokas mot eget kapital, se
  [Bokslut](01-komma-igang.md#bokslut-årsavslut).
- **Påminnelseavgift** – den avgift som föreslås när en betalningspåminnelse skrivs ut för en
  förfallen kundfaktura (standard: 60 kr). Se [7. Kundfakturor](07-kundfakturor.md).
- **Momsperiod** och **momsstartdatum** – styr om/hur [Momsrapport](03-momsrapport.md) visas och
  vilka transaktioner som räknas med.
- **E-postimport av bilagor** – aktivera och konfigurera Gmail (IMAP) eller Outlook/Exchange
  (OAuth2), inklusive vilken mapp som ska läsas av. Se [4. Bilagor](04-bilagor.md).
- **Utgående e-post** – företagets konto för att skicka fakturor och betalningspåminnelser via
  e-post, se nedan.
- **Aktivt/inaktivt** företag.

Endast användare kopplade till företaget med full behörighet (eller superuser) kan redigera det –
annars avvisas begäran med "Du har inte behörighet att redigera detta företag."

## Användare och läsbehörighet

Under **Företag → Användare** ser du vilka som har tillgång till företaget och kan lägga till
fler. Personen måste först ha registrerat ett eget konto i SaldoVibe; ange sedan e-postadressen
och välj roll:

- **Full behörighet** – kan bokföra, fakturera och ändra företagets inställningar.
- **Endast läsa** – ser alla sidor, rapporter och verifikationer men kan inte ändra något.
  Passar revisor eller redovisningskonsult som bara ska granska. Användaren ser en gul
  **Läsbehörighet**-markering i menyn, och varje försök att spara avvisas.

Du kan ta bort andra användare, men inte dig själv – på så vis finns det alltid minst en
användare med full behörighet kvar. Alla ändringar i användarlistan hamnar i auditloggen.

## Utgående e-post

Under **Utgående e-post** i redigeringsformuläret väljs hur företaget skickar fakturor och
betalningspåminnelser till kunder (se
[7. Kundfakturor](07-kundfakturor.md#skicka-faktura-och-påminnelse-via-e-post)). Två leverantörer
stöds:

- **SMTP** – ange avsändaradress, SMTP-server, port, användarnamn och lösenord.
  Standardporten 587 används med **STARTTLS** ikryssat; port 465 ger i stället implicit TLS.
  För Gmail: använd ett app-lösenord, precis som för bilageimporten. Ett sparat lösenord
  visas aldrig igen – lämna fältet tomt för att behålla det.
- **Microsoft 365 (Graph)** – återanvänder appregistreringen (Tenant ID, Client ID,
  Client Secret) som redan är ifylld under **E-postbilagor** (se
  [4. Bilagor](04-bilagor.md)). Utskick sker från brevlådan i **Avsändaradress** (lämnas den
  tom används e-postkontot för bilageimport). Utöver läsrollen behöver appen även
  sändrollen i Exchange Online:

  ```powershell
  New-ManagementRoleAssignment -Role "Application Mail.Send" `
    -App <appens service principal-id> `
    -CustomResourceScope <scope som omfattar avsändarbrevlådan>
  ```

  Utan rollen avvisas utskicket med ett fel som nämner "Application Mail.Send".

### Notiser till användare

Notisdigesten (den dagliga sammanfattningen till företagets användare, se notisklockan i
sidhuvudet) har ett eget kontoval under **Notiser till användare**:

- **Systemkontot (standard)** – systemets globala e-postkonto, konfigurerat av driftansvarig
  via miljövariabler (`docs/ops/environment-variables.md`). Är det inte konfigurerat skickas
  inga notismail (de skrivs bara till loggen).
- **Samma som utgående e-post** – återanvänder kontot som skickar fakturor och påminnelser.
- **Eget SMTP-konto** – separata SMTP-uppgifter bara för notiser.
- **Microsoft 365 (Graph), egen brevlåda** – återanvänder appregistreringen under
  E-postbilagor men skickar från en egen notisbrevlåda (t.ex. `notiser@kunden.se`).
  Rollen `Application Mail.Send` måste omfatta även den brevlådan – utöka management-scopens
  `RecipientRestrictionFilter` med `-or PrimarySmtpAddress -eq 'notiser@kunden.se'`, eller
  skapa en andra scope och rolltilldelning. Lämnas **Avsändaradress (notiser)** tom används
  e-postkontot för bilageimport.

## Flera företag och att växla mellan dem

Ett konto kan vara kopplat till flera företag. Byt aktivt företag via väljaren i sidhuvudet – se
[1. Komma igång](01-komma-igang.md#byta-aktivt-företag).

## Ta bort ett företag

Ett företag kan **bara tas bort om det saknar bokföringsdata** helt: inga räkenskapsår,
verifikationer, konteringsrader, leverantörsfakturor, kundfakturor eller lönekörningar. Finns något
av detta avvisas borttagningen med "Företaget kan inte tas bort eftersom bokföringsdata finns.
Avaktivera företaget istället för att bevara revisionsspår." – använd **Aktivt**-kryssrutan i
redigeringsformuläret för att stänga av företaget istället.

## Bank- & skattekällor

Se [5. Bank & skattekonto](05-bank-skattekonto.md) för hur bankkällor (inklusive skattekontot)
kopplas till bokföringskonton.

## Kontoplan och räkenskapsår

Se [1. Komma igång](01-komma-igang.md) för kontoplanen (auto-seedad BAS 2026) och reglerna kring
räkenskapsår (måste följa varandra, går inte att redigera i efterhand).

## Roller och behörigheter

Vissa åtgärder i appen kräver en viss lägsta roll utöver att vara inloggad och kopplad till
företaget:

| Åtgärd | Beskrivning | Lägsta roll |
|---|---|---|
| Hantera periodlås | Skapa/ändra periodlås, styrd återöppning | finance_admin |
| Radera räkenskapsår | Endast när inga bokningar finns | finance_admin |
| Avsluta räkenskapsår | Bokslutsflödet (bokslutsverifikationer, nytt räkenskapsår) | finance_admin |
| Radera företag | Endast när inga bokföringsobjekt finns | system_admin |
| Exportera SRU | Generera SRU-inlämningspaket | finance_admin |
| Exportera SIE4 | Generera fullständig SIE4-export | finance_admin |
| Markera lönekörning som rapporterad | AGI-rapportering till Skatteverket | finance_admin |
| Verifiera hashkedja | Kontrollera auditloggens integritet | finance_admin |
| Köra restore dry-run | Backupåterläsningstest | system_admin |

Se `docs/compliance/role-matrix.md` för den fullständiga och alltid uppdaterade listan.

## Nästa steg

Det här var sista kapitlet i användarhandboken. För drift/installation, se root-`README.md` och
`docs/ops/`.
