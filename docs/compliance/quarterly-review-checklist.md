# Checklista för kvartalsvis compliance-genomgång

## Huvudbok och integritet

- [ ] Kör `verify_audit_chain` och arkivera utdata.
- [ ] Granska verifikationsnummerluckor på compliance-dashboarden.
- [ ] Granska avvikelser för sent bokförda verifikationer och anteckna åtgärder.

## Rapportering och inlämning

- [ ] Validera SRU-förhandsrapporten för senast stängda period.
- [ ] Validera SIE/SI-importdiagnostiken: inga olösta fel.
- [ ] Verifiera att AGI-evidenspaket finns för alla rapporterade lönekörningar.

## Drift

- [ ] Bekräfta att de tre senaste månatliga återställningsrapporterna (dry-run) finns.
- [ ] Stickprova checksummor och tabellantal i återställningsrapporterna.
- [ ] Bekräfta att evidensartefakter bevaras enligt policy.

## GDPR

- [ ] Gå igenom `gdpr/breach-response-runbook.md` som en övning; anteckna brister.
- [ ] Granska DSAR-loggen: alla förfrågningar besvarade inom en månad; raderingsloggen
      (`gdpr-erasures.jsonl`) stämmer mot utförda förfrågningar.
- [ ] Stäm av `gdpr/processor-register.md` mot kodbasen: inget nytt utgående
      dataflöde utan en registerrad.
- [ ] Kontrollera `gdpr/ropa.md` mot drift (guard-testet för spårade modeller täcker
      modellerna; granska ändamål/mottagare manuellt).

## Styrning

- [ ] Bekräfta på nytt rolltilldelningarna för finance admin/system admin.
- [ ] Granska undantag/incidentärenden kopplade till låsta perioder.
- [ ] Signering av ekonomiansvarig och teknikansvarig.
