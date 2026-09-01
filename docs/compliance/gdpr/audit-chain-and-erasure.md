# Auditkedjan kontra rätten till radering (G-007, art. 17 kontra BFL)

Statusdatum: 2026-08-15

Ståndpunktsdokument: varför auditloggposter inte raderas på begäran, och vad som håller den
ståndpunkten försvarbar.

## Spänningen

Auditloggen (`auditlog`) är en append-only, hashkedjad liggare; databastriggrar blockerar
UPDATE/DELETE på tidigare poster, och varje posts hash kedjar till företagets föregående post
(en kedja per företag via `chain_key`; poster med `hash_version=1` från före uppdelningen
utgör ett fruset globalt legacy-segment) — att radera
eller redigera en post bryter kedjan by design (det är dess compliance-värde enligt BFNAR
2013:2). Postögonblicksbilderna innehåller personuppgifter: namn, adresser och kontaktuppgifter
i före-/eftertillstånd, samt den agerande användarens identitet per post. Art. 17 ger de
registrerade rätt till radering — de två kan inte båda gälla ovillkorligt.

## Ståndpunkten

1. **Auditposterna är behandlingshistorik över räkenskapsinformation.** BFNAR 2013:2 kräver
   att räkenskapsinformationens behandlingshistorik bevaras; posterna delar den lagstadgade
   grunden med de poster de beskriver. Radering nekas därför enligt **art. 17.3 b**
   (behandling som krävs enligt lag) under samma 7-årsfönster som den underliggande
   räkenskapsinformationen — se `retention-schedule.md` (aktivitet A7) och avslagsmallen i
   `dsar-runbook.md`.
2. **Högriskdata kommer aldrig in i kedjan.** Personnummer och e-postuppgifter maskeras
   *före* hashning via `sensitive_fields` i `auditlog/services.py`. Det som aldrig kom in i
   kedjan behöver aldrig raderas ur den.
3. **Maskeringen gäller endast framåt.** Ett fält som läggs till i `sensitive_fields` idag
   rensar inte historiska poster — de är oföränderliga. Därför måste varje nyspårad modell
   med högriskfält registreras i `sensitive_fields` **innan dess första post skrivs**. Detta
   upprätthålls av `auditlog.tests.SensitiveFieldsGuardTests`, som fallerar om någon spårad
   modell har ett fält som matchar `personal_identity_number`/`password`/`secret` utanför
   dess `sensitive_fields`.
4. **Anonymisering av livedata rör inte kedjan.** `gdpr_anonymize` (G-006) rensar aktuella
   rader; historiska ögonblicksbilder av namn/adresser blir kvar i kedjan enligt punkt 1.
   Anonymiseringen i sig blir en ny kedjepost, så åtgärden är bevisbar.
5. **Efter bevarandefönstret** förstörs auditposterna tillsammans med sitt räkenskapsår i
   gallringen efter bevarandetiden (G-008-designen, ~2032), som återförankrar kedjan i
   stället för att lämna en tyst lucka.

## Standardsvar på "radera mig ur auditloggen"

> Loggposterna utgör behandlingshistorik för räkenskapsinformation enligt BFNAR 2013:2 och
> omfattas av bokföringslagens arkiveringskrav (7 kap. 2 §). Radering nekas därför med stöd
> av artikel 17.3 b i dataskyddsförordningen till och med **[bevarandetidens slutdatum]**,
> varefter uppgifterna gallras. Personnummer och inloggningsuppgifter lagras aldrig i loggen.
> Dina aktuella uppgifter i systemet har anonymiserats.
