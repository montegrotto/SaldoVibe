# Specifikation för revisionsexportpaket

Version: 1.0

## Status

**Fas 1 + 2 + 3 implementerade** som `bookkeeping/export_bundle.py::generate_export_bundle`, nåbar
via `bookkeeping:export_bundle` (finance_admin, skyddad med `@require_compliance_action("export.bundle")`).
Paketet omfattar `manifest.json` + `bokforingsdata/` (SIE4) + `kontospecifikationer/` + `bilagor/` +
`banktransaktioner/` (en CSV per bank-/skattekontokälla, per fas 2) + `rapporter/` (per fas 3:
`moms/`, `agi/`, `lonebesked/`, en PDF vardera — se nedan) för valfritt datumintervall, bläddringsbart
via en medföljande `index.html`. Avsnitten `diagnostics/` och `audit/` nedan ingår **inte** i
fas 1/2/3 — se `SKATTEVERKET_COMPLIANCE_PLAN.md` rad R-013 / roadmap-punkt E4 för vad som skjutits
till en senare fas.

`rapporter/` innehåller medvetet bara perioder som redan var stängda/rapporterade innan exporten
kördes, aldrig en liveomräkning av en pågående period:

- `rapporter/moms/` — en PDF per `vat.VatCloseSnapshot` vars `period_start`/`period_end`
  överlappar exportintervallet (en momsperiod stängd via Rapporter → Momsrapport). En pågående
  period har inget snapshot ännu och utesluts.
- `rapporter/agi/` — en PDF per `payroll.PayrollRun` med `payment_date` i intervallet och
  `is_reported_to_skatteverket=True`. En avslutad men ännu inte rapporterad körning utesluts.
- `rapporter/lonebesked/` — en PDF per `payroll.SalaryRecord` vars `payroll_run.payment_date`
  ligger i intervallet och vars `payroll_run.is_finished=True`. Siffrorna i en oavslutad körning
  kan fortfarande ändras, så dess lönebesked tas inte med.

Varje underavsnitt har sin egen `index.html`, och en övergripande `rapporter/index.html` länkar
de tre.

## Mål

Tillhandahålla ett sammanhängande paket för revisorer med huvudboksevidens och
valideringsartefakter.

## Paketinnehåll

- `manifest.json`
- `ledger/`:
  - SIE4-exportfil
  - SRU-ZIP-utdata
- `diagnostics/`:
  - SIE-importdiagnostik (JSON)
  - SI-importdiagnostik (JSON)
  - SRU-förhandsrapport (JSON/CSV)
- `audit/`:
  - Verifieringsresultat för auditloggens hashkedja
- `payroll/`:
  - AGI-evidens-ZIP för rapporterade perioder — implementerat som `rapporter/agi/` (en PDF per
    rapporterad `PayrollRun`) och `rapporter/lonebesked/` (en PDF per lönebesked på en avslutad
    körning) i stället för en nästlad zip; momsdeklarations-PDF:er hamnar i `rapporter/moms/` på
    samma sätt

## Manifestfält

- `schema`
- `generated_at`
- `company`
- `period`
- `files[]` med:
  - `path`
  - `sha256`
  - `content_type`
  - `description`

## Integritetsregler

- Varje innehållsfil i paketet måste ha en SHA256-checksumma i `manifest.json`.
- Exportgenereringen ska vara deterministisk för samma period och samma källdata.
