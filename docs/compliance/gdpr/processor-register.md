# Biträdesregister (G-011, art. 28, 44)

Statusdatum: 2026-08-18

Varje tredje part som kommer i kontakt med personuppgifter som behandlas i SaldoVibe, med roll
och överföringsgrund. Utgående dataflöden i kodbasen och detta register måste stämma överens —
när en ny integration läggs till, lägg till en rad här i samma ändring.

| Part | Data som tas emot | Roll | Överföringsgrund / bedömning |
|---|---|---|---|
| Gmail / Microsoft 365 (e-posthämtning) | Meddelandemetadata och bilagor från företagets egen brevlåda (`attachments/management/commands/hamta_epostbilagor.py`) | **Kundens eget biträde** — anlitat under kundens Google-/Microsoft-avtal, inte ett SaldoVibe-underbiträde (se `roles-and-scope.md`) | Kundens befintliga PUB-avtal med sin e-postleverantör (båda erbjuder SCC-baserade EU-villkor); SaldoVibe ansluter bara med uppgifter kunden tillhandahåller |
| FreeTSA (RFC 3161-TSA) | Endast hashkedjans topphash (`auditlog/timestamping.py`, `AUDIT_CHAIN_TSA_URL`) | Ingen — **bedömd som ej personuppgift**: en SHA-256-kedjehash kan inte hänföras till någon person | Ingen överföring av personuppgifter; bedömningen dokumenterad här (G-011) |
| Skatteverket | AGI-XML (personnummer + lön), momsfiler; skattetabellslagningar via API innehåller personnummer (`payroll/skatteverket_api.py`) | Självständig myndighet — **inte ett biträde** | Rättslig förpliktelse; filer genereras lokalt och laddas upp av användaren |
| Peppol-accesspunkt (vid e-fakturering) | Motpartsuppgifter på fakturan (`invoicing/peppol.py`) | Överföringstjänst | Inom EU:s Peppol-nätverk; enligt accesspunktsavtalet |
| Hosting-/backupleverantör (endast hostad drift) | Hela databasen och media | Underbiträde | Måste vara EU-hostad eller omfattas av SCC; namnges i PUB-avtalet före driftstart |

**2026-08-18: ReInvGrabber borttagen ur detta register.** Dess OCR-fältextraktionslogik körs nu
in-process (`attachments/extraction_client.py`, beroendet `reinvgrabber-extraction` i
`requirements.txt`) i stället för att anropas som en separat tjänst — bilagebytes lämnar inte
längre SaldoVibe-processen för extraktion, så det finns inte längre någon tredje part eller
överföring att bedöma här. Detta stänger också den tidigare öppna punkten om att bekräfta dess
hostingplats.

Självhostade driftsättningar: endast kundens egen e-postleverantör, FreeTSA och Skatteverket är
aktuella — det finns ingen operatör och inget hosting-underbiträde.

Granskning: kvartalsvis (`quarterly-review-checklist.md`) — kontrollera att inget nytt utgående
flöde finns i kodbasen utan en rad här (greppa efter nya användningar av `requests`/externa
URL:er under appmodulerna).
