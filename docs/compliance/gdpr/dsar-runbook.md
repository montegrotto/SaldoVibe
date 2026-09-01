# DSAR-runbook (G-005/G-006, art. 15–17, 20)

Statusdatum: 2026-08-15

Hur en registrerads begäran besvaras från början till slut. Tidsfrist: **en månad** från
mottagandet (art. 12.3); logga mottagningsdatumet omedelbart. Roller enligt
`roles-and-scope.md` — vid hostad drift verkställer operatören på den personuppgiftsansvariges
dokumenterade instruktion; svaret till den registrerade kommer alltid från den
personuppgiftsansvarige (kundföretaget).

## 0. Mottagning

1. Verifiera beställarens identitet (mot kända e-post-/anställningsuppgifter — lämna aldrig
   ut data på en overifierad begäran).
2. Identifiera subjektstypen: applikationsanvändare, anställd, kundkontakt eller
   leverantörskontakt.
3. Registrera begäran i DSAR-loggen (mottagningsdatum, subjekt, typ av begäran, förfallodatum).

## 1. Begäran om tillgång/dataportabilitet (art. 15, 20)

Kör exportkommandot med matchande subjekt:

```bash
python manage.py dsar_export --employee <pk> --output utdrag.json
python manage.py dsar_export --customer <pk> --output utdrag.json
python manage.py dsar_export --supplier <pk> --output utdrag.json
python manage.py dsar_export --user <email> --output utdrag.json
```

Exporten innehåller subjektets stamdata, varje rad i applikationen som refererar dem (via
FK/M2M) samt auditlogghistoriken för dessa rader; för användare även deras egen aktivitetslogg
(endast metadata). Granska JSON-filen före utlämning — rader kan innehålla tredje parts
uppgifter (t.ex. en delad faktura) som ska maskeras i den kopia som lämnas ut.

Bilagor paketeras inte automatiskt: om kvitton/fakturor innehåller subjektets uppgifter,
hämta dem via bilagevyerna för de refererade raderna.

Komplettera JSON-filen med standardinformationen enligt art. 15.1–15.2: ändamål, kategorier,
mottagare och bevarande — allt detta finns i `ropa.md` + `retention-schedule.md`; hänvisa till
de relevanta aktivitetsavsnitten.

## 2. Begäran om rättelse (art. 16)

Använd de ordinarie redigeringsvyerna (formulär för anställd/kund/leverantör/användare).
Bokförda poster rättas via rättelseverifikationer, aldrig genom redigering — förklara detta i
svaret om rättelsen rör räkenskapsinformation.

## 3. Begäran om radering (art. 17)

Besluta per dataklass (`retention-schedule.md`):

**Klass A — räkenskapsinformation (avslå, med motivering).** Löneposter, fakturor, bilagor,
auditkedjeposter och annan bokföringsdata är undantagna från radering enligt art. 17.3 b så
länge bokföringslagen 7 kap. 2 § gäller. Punkter för svarsmallen:

- rättslig grund: BFL 7:2 tillsammans med art. 17.3 b;
- bevarandetidens **slutdatum** för de aktuella uppgifterna (slutet av det sjunde året efter
  det kalenderår då räkenskapsåret avslutades) — ange det konkret i svaret;
- att uppgifter utanför den lagstadgade omfattningen har raderats/anonymiserats (nedan);
- för auditloggen specifikt, hänvisa till `audit-chain-and-erasure.md`.

**Klass B — ingen lagstadgad bevarandeplikt (radera/anonymisera).** Kör:

```bash
python manage.py gdpr_anonymize --user <email> --yes       # användarkonto
python manage.py gdpr_anonymize --employee <pk> --yes      # kontaktfält
python manage.py gdpr_anonymize --customer <pk> --yes
python manage.py gdpr_anonymize --supplier <pk> --yes
```

- Anonymisering, inte borttagning: FK-integriteten och auditkedjan består; namn på
  räkenskapsinformation blir kvar (klass A).
- En kund/leverantör **helt utan transaktioner** kan i stället raderas direkt via den
  ordinarie raderingsfunktionen i gränssnittet.
- Åtgärden registreras i `<DATA_DIR>/gdpr-erasures.jsonl`; efter varje databasåterställning,
  kör om kommandona som listas där (se `restore-runbook.md`, G-014).
- Modelländringar fångas automatiskt av auditloggen; användarmodellen auditspåras inte, så
  för användaranonymiseringar **är** jsonl-posten plus DSAR-loggen dokumentationen av
  åtgärden.
- Åtkomst kräver shell-/management-kommandoåtkomst; motsvarande compliance-åtgärd är
  `gdpr.erase` (finance_admin) i `bookkeeping/compliance_policy.py`.

## 4. Avslut

1. Skicka svaret inom enmånadsfristen (förlängningsbar med två månader för komplexa
   begäranden, art. 12.3 — meddela i så fall inom den första månaden).
2. Komplettera DSAR-loggposten: vad som lämnades ut/raderades/avslogs, datum skickat, vem som
   verkställde.
3. DSAR-loggen granskas kvartalsvis (`quarterly-review-checklist.md`).
