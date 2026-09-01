# Runbook för personuppgiftsincidenter (G-010, art. 33–34)

Statusdatum: 2026-08-15

Verkställbara steg vid en misstänkt personuppgiftsincident. 72-timmarsklockan till IMY startar
när den **personuppgiftsansvarige blir medveten** om incidenten — vid hostad drift ska
operatören underrätta den personuppgiftsansvarige *utan onödigt dröjsmål* (art. 33.2), och den
personuppgiftsansvarige anmäler till IMY. Roller enligt `roles-and-scope.md`.

## 1. Upptäck och starta klockan

Upptäcktskällor:

- röda indikatorer på compliance-dashboarden (bruten auditkedja, förankringsfel — R-014);
- fel från `verify_audit_chain` / `verify_audit_chain_anchors`;
- hosting-/infralarm, borttappade enheter, felskickade exporter (en AGI-fil eller
  DSAR-export skickad till fel mottagare är en incident);
- rapporter från användare eller tredje part.

Omedelbart: anteckna **datum/tid för medvetenhet** i evidensloggen (nedan). Allt annat mäts
från denna tidsstämpel.

## 2. Begränsa

- Återkalla exponerade uppgifter (app-lösenord/OAuth-hemligheter för brevlådan via
  företagsformuläret; rotera `SALDOVIBE_FIELD_ENCRYPTION_KEY` och `DJANGO_SECRET_KEY` om
  nyckeln kan vara exponerad — se `docs/ops/environment-variables.md`).
- Inaktivera komprometterade användarkonton (`is_active = False`).
- Isolera värden/backupen om databasfilen kan ha läckt.

## 3. Bedöm

Besvara i evidensloggen:

1. Vilka personuppgifter, vilka registrerade, hur många? (Använd kategorierna i `ropa.md`.)
2. Var uppgifterna skyddade? En läckt DB-fil exponerar namn/adresser i klartext men **inte**
   personnummer eller e-postuppgifter (krypterade i vila, G-004/G-009) — detta förändrar
   riskbedömningen väsentligt och måste anges.
3. Pågående eller begränsad?

## 4. Anmäl — beslutsträd

- **Ingen risk för enskilda** (t.ex. läckta data var krypterade och nyckeln är säker):
  dokumentera resonemanget, ingen IMY-anmälan (undantaget i art. 33.1). Stanna vid steg 6.
- **Risk**: den personuppgiftsansvarige anmäler till **IMY inom 72 h** från medvetenhet
  (imy.se:s e-tjänst). En ofullständig anmälan inom fristen är bättre än en sen komplett —
  komplettera i efterhand (art. 33.4).
- **Hög risk** för de enskilda (t.ex. personnummer + löneuppgifter i klartext, eller
  e-postuppgifter användbara mot brevlådan): **underrätta även de registrerade** utan
  onödigt dröjsmål (art. 34), på klarspråk: vad som hänt, sannolika konsekvenser, vad de
  bör göra (t.ex. byta lösenord till brevlådan), kontaktpunkt.

## 5. Vid hostad drift

Operatören: begränsa (steg 2), underrätta sedan omedelbart berörd(a) personuppgiftsansvarig(a)
med fakta från steg 3. Den personuppgiftsansvarige äger steg 4 och 6. PUB-avtalet (se
`roles-and-scope.md`) reglerar detta.

## 6. Evidenslogg och avslut

För en tidsstämplad logg (art. 33.5 kräver dokumentation av *varje* incident, anmäld eller
inte): tidpunkt för medvetenhet, fakta, bedömning, beslut med motivering, skickade
underrättelser, åtgärder. Förvara under `docs/compliance/evidence/` eller i den
personuppgiftsansvariges incidentdokumentation.

Avslut: åtgärda grundorsaken, och en punkt i nästa kvartalsgenomgång
(`quarterly-review-checklist.md` innehåller en genomgångsövning av denna runbook).
