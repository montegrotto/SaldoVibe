# SaldoVibe – Användarhandbok

Den här handboken förklarar hur man använder SaldoVibe som slutanvändare: hur man kommer igång,
bokför löpande, hanterar fakturor, bank, lön, moms och rapporter. Den beskriver inte drift/installation
(se `README.md` i repo-roten och [docs/ops/](../ops/README.md)) eller de tekniska
compliance-kontrollerna (se `docs/compliance/`).

Kapitlen är ordnade efter menyn i vänsterkolumnen i appen.

## Innehåll

1. [Komma igång](01-komma-igang.md) – registrering, skapa företag, kontoplan, räkenskapsår
2. [Löpande bokföring](02-lopande-bokforing.md) – verifikationer, mallar, korrigeringar, SIE-import/export
3. [Momsrapport](03-momsrapport.md) – visa, exportera, stänga momsperiod
4. [Bilagor](04-bilagor.md) – uppladdning, e-postimport, mjuk radering, legal hold
5. [Bank & skattekonto](05-bank-skattekonto.md) – bankkällor, import, snabbbokföring
6. [Löner](06-loner.md) – anställda, lönekörning, AGI-rapportering och evidens
7. [Kundfakturor](07-kundfakturor.md) – kunder, artiklar, fakturering, betalning, kreditering
8. [Leverantörsfakturor](08-leverantorsfakturor.md) – leverantörer, registrering, betalning, QR
9. [Anläggningstillgångar](09-anlaggningstillgangar.md) – tillgångstyper, avskrivning
10. [Rapporter](10-rapporter.md) – balans, resultat, SRU, händelselogg, compliance-översikt
11. [Företagsinställningar](11-foretagsinstallningar.md) – företagsuppgifter, roller, borttagning
12. [Integritetspolicy](12-integritetspolicy.md) – vad som lagras om dig, kakor, dina rättigheter

Allt innehåll är verifierat mot faktisk kod (views/forms/models), inte bara mot
`docs/system-replication-spec.md` som är en aspirationsspec för att bygga om systemet – texten
beskriver vad appen faktiskt gör idag, inklusive de exakta felmeddelanden användaren stöter på.
