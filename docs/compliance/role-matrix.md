# Rollmatris (policy-som-kod-baslinje)

## Roller

- `finance_operator`: daglig bokföring och fakturahantering
- `finance_admin`: stänger perioder, kör exporter, hanterar compliance-flöden
- `system_admin`: infrastruktur och nödåtkomst

Utöver compliance-rollerna har varje företagsmedlemskap (`bookkeeping.CompanyMembership`) en
roll: **full behörighet** (standard) eller **endast läsa** (t.ex. revisor). Läsrollen ser allt i
företaget men varje POST spärras centralt i `company_scope.require_company`; företagets
inställningar och användarlista kräver full behörighet (`can_edit_company`).

## Känsliga åtgärder

| Åtgärdsnyckel | Beskrivning | Lägsta roll |
|---|---|---|
| `period_lock.manage` | Skapa/uppdatera periodlås samt kontrollerad återöppning | finance_admin |
| `accounting_year.delete` | Radera räkenskapsår (endast utan bokförda poster) | finance_admin |
| `accounting_year.close` | Kör bokslutsguiden (S1/S2-verifikationer, skapande av nästa år) | finance_admin |
| `company.delete` | Radera företag (endast utan bokföringsdata) | finance_admin |
| `export.sru` | Generera SRU-inlämningspaket | finance_admin |
| `export.sie4` | Generera fullständig SIE4-export | finance_admin |
| `export.bundle` | Generera det samlade revisions-/exportpaketet | finance_admin |
| `import.sie` | Importera SIE/SI-filer (skapar konton, skapar/ersätter importerade verifikationer) | finance_admin |
| `vat.close_period` | Stäng en momsperiod (snapshot + omföringsverifikation) och generera eSKD-filen | finance_admin |
| `payroll.report_mark` | Markera lönekörning som rapporterad till Skatteverket (inkl. nedladdning av AGI-XML/evidens) | finance_admin |
| `audit.verify_chain` | Verifiera auditloggens hashkedja (compliance-dashboarden kör detta inline) | finance_admin |
| `restore.dry_run` | Kör återställningstestets evidensjobb | system_admin |
| `voucher_series.manage` | Ändra vilken verifikationsserie en transaktionskälla bokför i | finance_admin |
| `gdpr.erase` | Anonymisera en användare eller persondata utanför bevarandetiden (kommandot `gdpr_anonymize`) | finance_admin |

## Efterlevnad

Varje åtgärdsnyckel med en HTTP-endpoint upprätthålls av
`bookkeeping.compliance_policy.require_compliance_action("<nyckel>")` på vyn, och var och en har
ett test för "blockerad för icke-finance_admin". Två rader saknar HTTP-yta: `restore.dry_run`
(`compliance_restore_dry_run`) och `gdpr.erase` (`gdpr_anonymize`) finns bara som
management-kommandon och upprätthålls därför av shell-/deployåtkomst (system_admin per
definition), inte av en vydekorator.

De kvarvarande `is_superuser`/`is_staff`-kontrollerna i `bookkeeping/company_scope.py` och
`bookkeeping/views/companies.py` är tenant-medlemskapsavgränsning (vilka företag en användare kan
se eller är medlem i), inte compliance-rollkontroller — de lämnas som de är.
