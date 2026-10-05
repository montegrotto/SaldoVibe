# SaldoVibe för iPhone

SwiftUI-app (iOS 18+) mot JSON-API:et i `api/` på servern. Inga tredjepartsberoenden; hela
projektet är `SaldoVibe.xcodeproj` med en synkroniserad mapp, så nya Swift-filer under
`SaldoVibe/` plockas upp utan att projektfilen ändras.

Vad appen gör och hur den används står i användarhandboken: `docs/user-guide/13-mobilappen.md`
(rendereras under **Hjälp → Mobilappen** på webben).

## Bygga och köra

Öppna `ios/SaldoVibe.xcodeproj` i Xcode 16 eller senare och kör på en simulator eller iPhone.

- **Simulator:** inget mer behövs. Logga in mot en lokal `manage.py runserver` med adressen
  `http://127.0.0.1:8000` (simulatorn delar Macens nätverk; klartext-HTTP tillåts bara mot
  lokala adresser via `NSAllowsLocalNetworking`).
- **Egen iPhone:** välj ditt team under *Signing & Capabilities* (projektet lämnar
  `DEVELOPMENT_TEAM` tomt), anslut telefonen och kör. Första gången: lita på utvecklaren under
  Inställningar → Allmänt → VPN och enhetshantering.
- **TestFlight:** se nedan.

Kamerafunktionerna (dokumentskannern och QR-läsaren) finns bara på en riktig enhet; i
simulatorn erbjuds **Välj foto** och lösenordsinloggning.

Från terminalen:

```bash
cd ios
xcodebuild -project SaldoVibe.xcodeproj -scheme SaldoVibe \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' build
```

Signering får inte stängas av (`CODE_SIGNING_ALLOWED=NO`) för simulatorbyggen som ska köras:
utan "Sign to Run Locally" saknar appen entitlements och nyckelringen vägrar spara
inloggningen (status -34018), så appen glömmer sessionen vid varje start.

## TestFlight

Engångsförberedelser:

1. Xcode → Settings → Accounts: logga in med det Apple-ID som är med i utvecklarteamet
   `7MV2FUVY65` (projektets `DEVELOPMENT_TEAM`). Ett "Personal Team" räcker inte – det kan inte
   ladda upp till App Store Connect. Är det ett annat team, byt `DEVELOPMENT_TEAM` i
   `project.pbxproj` och `teamID` i `ExportOptions.plist`.
2. [App Store Connect](https://appstoreconnect.apple.com) → Appar → **+** → Ny app: iOS, namn
   *SaldoVibe*, primärt språk svenska, bundle-id `se.saldovibe.app` (dyker upp i listan efter
   första arkiveringen, då Xcode registrerar App-ID:t; annars registrera det under Certificates,
   Identifiers & Profiles), valfritt SKU t.ex. `saldovibe-ios`.
3. Under appens flik **TestFlight** → Intern testning: skapa en grupp och lägg till dig själv
   (App Store Connect-användare). Interna testare behöver ingen granskning; externa gör det.

Sedan, varje gång:

```bash
ios/scripts/testflight.sh
```

Skriptet arkiverar i Release, låter automatisk signering skapa distributionscertifikat och
profil (`-allowProvisioningUpdates`) och laddar upp med `ExportOptions.plist`
(`app-store-connect`, `destination upload`). Byggnumret blir aktuell minut (`yyyymmddHHMM`) så
att varje uppladdning är unik; versionen är `MARKETING_VERSION` i projektet. Efter några
minuters bearbetning finns bygget under TestFlight i App Store Connect och i TestFlight-appen
på telefonen. Samma sak från Xcode: Product → Archive → Distribute App → TestFlight & App Store.

Redan på plats för uppladdningen: 1024-pixelsikon (`Assets.xcassets/AppIcon.appiconset`, rendrerad
från `static/brand/saldovibe-mark.svg`), `PrivacyInfo.xcprivacy` (UserDefaults, skäl CA92.1, ingen
spårning, ingen insamling) och `ITSAppUsesNonExemptEncryption = NO` (bara vanlig HTTPS), så
exportfrågan ställs inte per bygge.

## Inloggning och API

Appen får en personlig token (`accounts.ApiToken`) antingen via `POST /api/v1/auth/login/`
(serveradress + e-post + lösenord) eller genom att skanna QR-koden på webbsidan **Mobilappen**
(`/konton/appen/`), som är länken `saldovibe://login?server=…&token=…`. Token lagras i
nyckelringen; aktivt företag i UserDefaults. Alla anrop skickar `Authorization: Bearer <token>`
och `X-Company-Id`. Endpoints: se `api/urls.py`; alla skrivningar går genom samma Django-formulär
och tjänster som webben.

App Transport Security släpper bara fram https till domännamn; IP-adresser, `localhost`, `.local`
och okvalificerade namn får gå över http. Säger en QR-kod `http://` för ett domännamn provar appen
https först. Servern bör ha `SALDOVIBE_PUBLIC_URL` satt så att QR-koden blir rätt från början.

## Utvecklingsknep (bara DEBUG-byggen)

- `-tab receipts|expenses|invoices|more`, `-invoiceKind 0|1` och `-report resultat|balans` som
  startargument öppnar en given flik eller rapport – används för skärmdumpar via
  `xcrun simctl launch … -tab receipts`.
- `-loginURL 'saldovibe://login?server=…&token=…'` loggar in samma väg som en skannad QR-kod.
  (`xcrun simctl openurl` går inte: simulatorn visar en "Öppna i SaldoVibe?"-dialog som
  ingenting kan trycka på.)
- Miljövariablerna `SALDOVIBE_DEV_SERVER`, `SALDOVIBE_DEV_EMAIL`, `SALDOVIBE_DEV_PASSWORD`
  (med prefixet `SIMCTL_CHILD_` när de sätts för `simctl launch`) loggar in med lösenord vid
  start om ingen session finns.

Skärmdumpsrunda mot en engångsdatabas (se `.claude/skills/run-app/SKILL.md` för servern):

```bash
xcrun simctl install booted <DerivedData>/Build/Products/Debug-iphonesimulator/SaldoVibe.app
xcrun simctl keychain booted reset
xcrun simctl launch --terminate-running-process booted se.saldovibe.app -loginURL "saldovibe://login?server=http%3A%2F%2F127.0.0.1%3A8731%2F&token=$TOKEN"
sleep 10 && xcrun simctl io booted screenshot oversikt.png
```
