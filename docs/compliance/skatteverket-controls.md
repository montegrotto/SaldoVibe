# Kontrollkatalog Skatteverket

Statusdatum: 2026-07-07

## Kontrollregister

| Kontroll-ID | Kontroll | Frekvens | Ansvarig | Evidensartefakt |
|---|---|---|---|---|
| SKV-C-001 | Oföränderligt verifikationsspår via rättelseverifikationer | Löpande | Bokföring | Transaktionslänkar (`correction_of`) + hashkedjad auditlogg |
| SKV-C-002 | Periodlås upprätthålls vid bokföring/import | Löpande | Bokföring | Blockerad bokföring + periodlåsposter |
| SKV-C-003 | Kontroll av verifikationsnumrens integritet | Veckovis | Finance admin | Nummerlucke-KPI på compliance-dashboarden |
| SKV-C-004 | SRU-förhandsvalidering före export | Vid export | Rapportering | SRU-valideringsrapport JSON/CSV |
| SKV-C-005 | Valideringsdiagnostik för SIE/SI-parsern | Vid import | Bokföring | Importdiagnostik som JSON-artefakt |
| SKV-C-006 | AGI-evidenspaket för lönekörningar | Vid rapportering | Lön | AGI-evidenspaket (ZIP) |
| SKV-C-007 | Integritet i auditloggens hashkedja | Månadsvis | Säkerhet/Ekonomi | Utdata från `verify_audit_chain` |
| SKV-C-008 | Verifiering av återställningstest (dry-run) | Månadsvis | Drift | Återställningsrapport (JSON) i `docs/compliance/evidence/restore-tests/` |

## Minsta bevarandetid för evidens

- Spara månatliga återställningsrapporter i minst 24 månader.
- Spara AGI-evidenspaket och SRU/SIE-exportdiagnostik per redovisningsperiod.
- Spara verifieringsloggar för hashkedjan från varje månadskörning.
