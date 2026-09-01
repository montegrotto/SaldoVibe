# DPIA-screening (G-013, art. 35)

Statusdatum: 2026-08-15

En sidas screening för varje ny integritetskänslig funktion, **genomförd före lansering**. En
fullständig konsekvensbedömning (DPIA, art. 35) krävs när behandlingen "sannolikt leder till
hög risk" — använd IMY:s triggerlista: systematisk storskalig övervakning, storskaliga
särskilda kategorier eller mycket personliga uppgifter, sårbara registrerade, ny teknik,
matchning/sammanslagning av datamängder. Screeningarna samlas i denna fil, nyast först.

## Mall

```markdown
### <Funktion> — <datum>

- Funktion och ändamål:
- Berörda personuppgifter (kategorier, registrerade, skala):
- ROPA-aktivitet: (lägg till/uppdatera ropa.md i samma ändring)
- Risker för de enskilda:
- Befintliga skyddsåtgärder:
- Fullständig DPIA utlöst? Ja/Nej — resonemang mot IMY:s triggerlista.
```

---

## Genomförda screeningar

### Lön (personnummerbehandling) — retroaktivt dokumenterad 2026-08-15

- **Funktion och ändamål:** löneberäkning, lönebesked, AGI-rapportering till Skatteverket.
- **Personuppgifter:** anställds namn, personnummer, adress, löne- och skatteuppgifter.
  Skala: småföretags egna anställda (ental till tiotal per företag) — inte storskaligt.
- **ROPA-aktivitet:** A2.
- **Risker:** identitetsmissbruk om personnummer läcker; löneuppgifter är känslighetsnära.
- **Skyddsåtgärder:** personnummer krypterat i vila (Fernet, G-004) med blint HMAC-index;
  maskerat till `19900101-XXXX` överallt utom i AGI-XML och lönebesked; maskerat i
  auditkedjan; åtkomst företagsavgränsad.
- **Fullständig DPIA utlöst? Nej** — lagstadgad behandling (SFL/BFL), liten skala per
  personuppgiftsansvarig, ingen övervakning, inga särskilda kategorier. Screena om ifall
  SaldoVibe drivs centralt för många företag (aggregerad skala ändrar bedömningen).

### E-posthämtning (brevlådeåtkomst) — retroaktivt dokumenterad 2026-08-15

- **Funktion och ändamål:** automatisk hämtning av leverantörsfakturabilagor från en
  företagsbrevlåda (`attachments/email_import.py`).
- **Personuppgifter:** e-postuppgifter (inloggning); avsändaradresser och
  meddelandemetadata; bilageinnehåll. Brevlådan kan innehålla orelaterad personlig
  korrespondens.
- **ROPA-aktivitet:** A6.
- **Risker:** inloggningsuppgifterna ger åtkomst till hela brevlådan — en läcka når långt
  bortom SaldoVibes egna data; överinsamling om hämtningen läser mer än fakturamejl.
- **Skyddsåtgärder:** uppgifterna krypterade i vila (G-009) och maskerade i auditloggen;
  hämtningen begränsad till en konfigurerad mapp; en dedikerad fakturabrevlåda
  rekommenderas (appvägledning); e-postleverantören är kundens eget biträde
  (`processor-register.md`).
- **Fullständig DPIA utlöst? Nej** — företaget läser sin egen brevlåda för ett ändamål;
  ingen systematisk övervakning av enskilda. Screena om ifall hämtningens omfattning
  vidgas (t.ex. alla mappar, innehållsindexering).

### OCR-extraktion via ReInvGrabber — retroaktivt dokumenterad 2026-08-15, uppdaterad 2026-08-18

- **Funktion och ändamål:** fältextraktion ur uppladdade kvitton/fakturor
  (`attachments/extraction_client.py`).
- **Personuppgifter:** vad dokumentet råkar innehålla — namn, adresser, ibland partiella
  kortnummer på kvitton. Sedan 2026-08-18 körs extraktionslogiken in-process (beroendet
  `reinvgrabber-extraction`); inga bilagebytes överförs längre till en separat tjänst.
- **ROPA-aktivitet:** A5.
- **Risker:** inga utöver ordinarie in-process-hantering — den tidigare
  tredjepartsöverföringsrisken gäller inte längre.
- **Skyddsåtgärder:** ej tillämpligt — biträdesregisterposten och dess öppna
  hostingplatspunkt (G-011) löstes genom att den externa överföringen togs bort, inte genom
  att den mildrades.
- **Fullständig DPIA utlöst? Nej** — enstaka affärsdokument, ingen profilering, inga
  särskilda kategorier by design. Screena om ifall extraktionen börjar lagra/träna på
  inskickade dokument.
