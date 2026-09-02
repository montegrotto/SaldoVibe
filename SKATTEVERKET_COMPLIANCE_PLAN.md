# Skatteverket-efterlevnadsmatris och implementationsbacklogg

Statusdatum: 2026-08-14
Omfattning: bokföringsplattformen SaldoVibe (Django)

Detta dokument är en implementationsfokuserad efterlevnadsplan för förväntningarna på svensk
bokföringsprogramvara. Det är teknisk vägledning, inte juridisk rådgivning.

## 1) Matris krav-till-implementation

Teckenförklaring:
- Status: Implemented / Partial / Missing
- Prioritet: P0 (kritisk), P1 (hög), P2 (medel)

| ID | Kravområde | Förväntat utfall | Aktuell implementationsevidens | Status | Gap | Rekommenderad implementationsändring | Testtäckning att lägga till | Acceptanskriterier | Prioritet |
|---|---|---|---|---|---|---|---|---|---|
| R-001 | Verifikationer ska vara spårbara och bevarade | Bokförda verifikationer raderas inte destruktivt i normal drift | Årsradering (`bookkeeping/views/accounts.py`) blockeras när året innehåller verifikationer; företagsradering (`bookkeeping/views/companies.py`) blockeras när någon bokföringsdata finns ("avaktivera istället"); triggrar på DB-nivå (`bookkeeping/migrations/0036_lock_posted_transactions.py`, `auditlog/migrations/0004_lock_audit_log_entries.py`, SQLite + Postgres) blockerar UPDATE/DELETE på transaktioner i låsta perioder och på auditloggrader även via queryset-`.update()`/`.delete()` eller direkt SQL, vilket stänger admin-/ORM-kringgåendet; SIE-omimportens ersättningssteg (`bookkeeping/views/imports.py`) raderar nu endast transaktioner med `source=sie_import` utanför låsta perioder — nativt bokförda verifikationer (manuella, faktura, lön, moms, …) kan aldrig raderas av en import | Implemented | Transaktioner skapade via SIE-import till en olåst period kan fortfarande ersättas av en bekräftad omimport (avsiktligt: omtag av en misslyckad migrering; källsystemet har originalen, åtgärden bekräftas i UI och auditloggas) — progressiv månadslåsning tar bort även detta | Om en striktare regim önskas, lås perioder snarast efter att de stängts (R-002-UI) — en låst period är oföränderlig på DB-nivå | `bookkeeping.tests.test_ledger_immutability_triggers`; `test_sie_import_never_deletes_natively_booked_transactions`, `test_sie_import_preserves_locked_period_transactions_when_replacing`, `test_sie_import_requires_confirmation_when_imported_transactions_exist` | Ingen nativt bokförd verifikation kan raderas via någon app-endpoint eller ORM-väg; rättelser skapar balanserande omföringstransaktioner länkade till originalet (R-006) | P0 |
| R-002 | Periodlåsning och stängningskontroller | Stängda perioder kan inte ändras/importeras till utan uttrycklig kontrollerad återöppning | `PeriodLock`-modell + `is_date_locked`/`is_range_locked` (`bookkeeping/period_locking.py`) upprätthålls i manuell bokföring (`transaction_add`), omföring (`transaction_reverse`), SIE/SI-import och bokföring från andra appar (`payroll/models.py`, `supplier_invoices/models.py`, `invoicing/models.py`). Nytt användargränssnitt på `bookkeeping:period_lock_list` (skyddat med `@require_compliance_action("period_lock.manage")`, endast finance_admin) låter en användare låsa ett helt `AccountingYear` i en åtgärd (`period_lock_lock_year`) eller låsa en passerad kalendermånad i taget via listan "Föreslagna månader att låsa" (`suggest_monthly_periods`), enligt BFNAR 2013:2:s förväntan om progressiv periodstängning; det finns avsiktligt inget UI för godtyckliga datumintervall — ett helt år eller en hel månad är de enda periodformer en verksamhet någonsin behöver stänga, så `period_lock_create` accepterar bara det månadsförslagsknapparna skickar (även om `PeriodLockForm`/den underliggande modellen fortfarande validerar intervall/överlapp generiskt, ifall endpointen någonsin anropas direkt). Återöppning (`period_lock_reopen`) kräver en `reopened_reason` och registrerar `reopened_by`/`reopened_at`; omlåsning (`period_lock_relock`) kräver ett nytt `reason`. `bookkeeping.periodlock` lades till i `auditlog.TRACKED_MODELS` så att varje låsning/återöppning/omlåsning hashkedje-auditloggas med före-/efterfältdiffar | Implemented | Fortfarande en enda `is_locked`-boolean i stället för de ursprungligen tänkta nivåerna öppen/mjuk/hård — behövs inte i praktiken eftersom överlappande intervall avvisas och återöppning/omlåsning redan bär separata motiverade auditspår; inget automatiskt månadslåsningsjobb (låsning är en medveten användaråtgärd, by design) | Om en striktare regim någonsin behövs, lägg till en mjuk/hård-distinktion (t.ex. mjuk = varna, hård = blockera) | `bookkeeping.tests.PeriodLockManagementTests` (11 tester: skapande kräver skäl, låser en period, avvisar intervall utanför året och överlappande intervall, låser ett helt år, återöppning kräver skäl, återöppning/omlåsning tur-och-retur rensar/återfyller återöppningsfälten, icke-finance_admin blockeras, lås-/återöppningsändringar auditloggas, månadsförslagen utesluter innevarande/framtida månad och speglar befintliga lås); befintliga `bookkeeping.tests.SIEImportTests.test_sie_import_skips_locked_periods` passerar fortfarande | En finance_admin kan låsa ett helt räkenskapsår eller en enskild kalendermånad med skäl, i UI:t, i en åtgärd; varje låsning/återöppning/omlåsning auditloggas med aktör och skäl; bokföring/import till en låst period blockeras överallt där verifikationer skapas | P0 |
| R-003 | Verifikationsnumrens integritet | Unika, ordnade verifikationsnummer per serie med luckkontroller | `VoucherSeries` (race-säker, `select_for_update`) + `VoucherSeriesRule` (mappning på företagsnivå av transaktionskälla → seriekod, `bookkeeping/models.py`); serie löses automatiskt per ursprungsmodul (manuell/bank/kundfaktura/leverantörsfaktura/lön/moms/anläggningstillgång/SIE-import), redigerbar på `bookkeeping:voucher_series_settings`; rättelser ärver originalverifikationens serie; `bookkeeping.voucherseriesrule` ligger nu i `auditlog.TRACKED_MODELS` så att ändringar av seriemappningen auditloggas, enligt BFNAR 2013:2 punkt 9.6/9.16 (systemdokumentationen/behandlingshistoriken ska visa hur verifikationsserierna är indelade och när sådana ändringar gjordes) — detta var en lucka i det ursprungliga R-003-arbetet, funnen vid genomläsning av BFNAR 2013:2 och åtgärdad samma dag | Implemented | Flerserieschemat fanns sedan fas 1 men `Transaction.save()` hårdkodade ovillkorligen `voucher_series = "A"`, vilket gjorde varje annan serie till död kod | Tog bort den hårdkodade överskrivningen i `Transaction.save()`; serien löses nu via `VoucherSeriesRule.resolve_series_code()` per transaktionskälla, med rättelser som ärver originalverifikationens serie | ej tillämpligt — implementerat; verifierat från början till slut (numrering per källa, mappningsändring i drift, rättelse som ärver originalets serie) | Systemet tilldelar löpande, lucklösa verifikationsnummer per serie; dubbletter omöjliga; varje företag kan konfigurera vilken serie en given transaktionskälla bokförs i | P0 |
| R-004 | Auditspår för affärshändelser | Väsentliga bokföringsändringar loggas med aktör, tidsstämpel, före/efter | SHA-256-hashkedja (`prev_hash`/`entry_hash`) i `auditlog/services.py` + `verify_audit_chain`; externt RFC 3161-tidsstämpelankare i `auditlog/timestamping.py` (`AuditChainAnchor`-modell, kommandona `anchor_audit_chain`/`verify_audit_chain_anchors`, körs månadsvis) | Implemented | `reseal_audit_chain --apply` ensamt kan fortfarande få en internt manipulerad kedja att se konsistent ut för `verify_audit_chain`; det externa ankaret stänger detta genom att intyga topphashen hos en oberoende TSA, verifierat skarpt: manipulerade en post, omförseglade, bekräftade att `verify_audit_chain` lurades medan `verify_audit_chain_anchors` ändå upptäckte det | Skicka kedjetoppens hash till en extern RFC 3161-TSA enligt schema och lagra det signerade svaret (`AuditChainAnchor`); verifiera tokens med `openssl ts -verify` i stället för `rfc3161ng`:s egen kontroll, som bara stödjer RSA-signerade tokens och kastar fel på FreeTSA:s ECDSA-signerade svar | `test_anchor_audit_chain_creates_anchor_for_new_tip`, `test_verify_audit_chain_anchors_detects_tampering_that_survives_a_reseal`, plus en manuell skarp körning mot den riktiga FreeTSA-tjänsten och återställning av dev-databasen till ursprungsläget efteråt | Manipulera-och-omförsegla upptäcks fortfarande av `verify_audit_chain_anchors`, oberoende av vad `verify_audit_chain` ensamt rapporterar | P1 |
| R-005 | Bevarande av bilagor/underlag | Underlag bevaras och länkas till bokföringshändelser, och kan inte läggas till eller tas bort från ett dokument när dess period är låst | `TransactionAttachment` har en matchande `attachments`-M2M på `bookkeeping.Transaction`, `invoicing.Invoice` och `supplier_invoices.SupplierInvoice`. Länkningen är inte längre knuten till bokföringsögonblicket: `attachments/view_helpers.py::add_attachments`/`remove_attachment` ligger bakom varje `*_attachment_add`-/`*_attachment_remove`-vy i `bookkeeping`, `invoicing`, `supplier_invoices` och `expenses`, och båda kontrollerar `bookkeeping.period_locking.is_date_locked(company, period_date)` — en bilaga kan kopplas till eller från ett redan bokfört dokument så länge dess period är öppen, och blockeras när perioden låsts (ersätter den tidigare `legal_hold`-vid-bokföring-designen, borttagen i migrationen `attachments/migrations/0007_remove_transactionattachment_legal_hold.py`) | Implemented | `invoicing:invoice_detail` och `bookkeeping:transaction_detail` stödjer redan koppling/frånkoppling på befintliga (även bokförda) poster via panelen; det kvarvarande gapet ligger uppströms om denna rad — permanent bevarande vilar helt på att periodlås faktiskt tillämpas (R-002) | — | `invoicing.tests`: `test_selected_attachment_is_linked_when_invoice_is_booked`, `test_selected_attachment_is_linked_for_draft_invoice`, `test_attachment_can_be_added_to_a_booked_invoice_in_an_open_period`, `test_attachment_cannot_be_added_or_removed_when_period_is_locked`; `bookkeeping.tests.test_transactions`: `test_selected_attachment_is_linked_on_post`, `test_attachment_can_be_added_to_a_posted_transaction_in_an_open_period`, `test_attachment_cannot_be_added_or_removed_when_period_is_locked`; verifierat från början till slut genom rendering av de riktiga faktura-/transaktions-/väljarsidorna | Bilagor kan kopplas till eller från en bokförd leverantörsfaktura, kundfaktura eller manuell verifikation medan dess period är öppen, och ingen av åtgärderna är möjlig när perioden låsts | P0 |
| R-006 | Rättelse framför omskrivning | Historiska fel rättas genom uttryckliga rättelseposter | `Transaction.correction_of`-FK (`bookkeeping/models.py`) + omföringsvyn `transaction_reverse` (`bookkeeping/views/transactions.py`) skapar en balanserande rättelse länkad till originalet; rättelser ärver originalverifikationens serie (R-003); periodlås upprätthålls på omföringsdatumet; mutation på plats av bokförda transaktioner blockeras på DB-nivå (R-001-triggrarna) | Implemented | — | — | Omföringstesterna i `bookkeeping.tests.test_transactions`; `test_ledger_immutability_triggers` | Varje rättelse refererar sin originalverifikation och bevarar full historik; bokförd data kan inte muteras på plats | P0 |
| R-007 | SIE-interoperabilitet | Import- och exportstöd tillräckligt för utbyte med redovisningskonsult/revisor | Export: `generate_sie4_content` skriver `#KONTO`, `#SRU`, `#IB`/`#UB`, `#RES`, `#VER`/`#TRANS` (verifierat via dubbel bokförings-identiteten). Import (`sie_import`): `parse_sie_accounts` namnger autoskapade konton från filens `#KONTO` i stället för en generisk platshållare; `parse_sie_balances`/`reconcile_closing_balances` jämför filens `#UB` per konto mot huvudbokens beräknade utgående balans efter import och lyfter avvikelser som varning + diagnostikpost | Implemented | `si_import` (det separata kontoommappningsflödet) använder fortfarande generiska platshållarnamn — lägre prioritet eftersom användaren där redan väljer målkonton explicit; inget `#DIM`-/`#OBJEKT`-dimensionsstöd i någondera riktningen | Applicera samma `#KONTO`-baserade namngivning på `si_import`:s kontoommappningsflöde; lägg till `#DIM`-/`#OBJEKT`-dimensionsparsning vid import och export om kostnadsställesdimensioner någonsin behövs | `test_sie4_export_includes_ib_ub_res_sru`; `test_sie_import_uses_konto_names_for_new_accounts`; `test_sie_import_warns_when_ub_does_not_match_computed_balance`; `test_sie_import_does_not_warn_when_ub_matches_computed_balance`; verifierat från början till slut med en riktig export→import-till-nytt-företag-rundtur | Exporten innehåller ingående/utgående balanser och periodens resultat per konto; importen använder riktiga kontonamn och flaggar varje utgående balans-avvikelse efter import | P1 |
| R-008 | Momsrapporteringens korrekthet och stängning | Momsruteberäkning och periodstängning är reproducerbara och reviderbara | `VatCloseSnapshot` (`vat/models.py`) persisterar rutor, käll-transaktions-id:n, ett SHA-256-`source_fingerprint` av periodens momsrader samt användare/tid vid stängning; compliance-dashboarden (`bookkeeping/views/dashboard.py`) räknar om varje snapshots fingeravtryck vid inläsning och flaggar "Momsstängningar där underlaget ändrats efter stängning"; stängning av en olåst period uppmanar nu användaren att periodlåsa den (låsta perioder är sedan oföränderliga på DB-nivå enligt R-001); `vat.vatclosesnapshot` auditloggas så att omstängningar (som skriver över snapshotet via `update_or_create`) syns i behandlingshistoriken | Implemented | Drift efter stängning upptäcks (dashboarden) snarare än hårdblockeras — hårdblockering är exakt vad ett periodlås ger, så stängningsflödet styr användaren dit i stället för att duplicera mekanismen | — | `test_vat_period_can_be_closed_by_creating_transaction` (snapshotfält + låsuppmaning), `bookkeeping.tests.test_dashboard.ComplianceDashboardVatDriftTests` | Momsstängning skapar evidens med verifierbart fingeravtryck; all drift efter stängning lyfts på compliance-dashboarden; låsning av perioden förhindrar drift helt | P0 |
| R-009 | SRU-exportstöd | SRU-utdata som stöd för deklaration | SRU-rapport/nedladdning i `bookkeeping/views/sru.py` med förhandsdiagnostik före export; `Account.save()` autotilldelar nu SRU-koden från den delade BAS→SRU-mappningen (`bookkeeping/sru_lookup.py`, används även av `populate_sru_codes`) vid skapande när fältet lämnats tomt, vilket stänger luckan där egna/importerade konton i tysthet saknade kod tills en admin körde om backfill-kommandot | Implemented | `populate_sru_codes` behövs fortfarande för att backfylla konton skapade före denna ändring, eller för att reparera koder efter en mappningsuppdatering (`--overwrite`) | ej tillämpligt — implementerat; behåll `populate_sru_codes` för backfyllning av befintliga konton och reparationer efter mappningsändringar | Manuell verifiering: BAS-mappade kontonummer autofylls korrekt, explicita/manuella koder skrivs aldrig över, omappade intervall lämnas tomma | Nya konton får korrekt SRU-kod utan manuellt steg; befintliga konton påverkas inte om de inte uttryckligen backfylls | P2 |
| R-010 | Evidens för lönerapportering | Lönekörningsrapportering kan evidensbeläggas och kontrolleras | Evidenssnapshot + rapporterad-markering i `payroll/models.py`; riktig AGI-XML-filgenerering i `payroll/agi.py` (Skatteverkets eSKD-instansschema för "arbetsgivardeklaration på individnivå", verifierad med `xmllint --schema` mot de officiellt publicerade XSD:erna och korskontrollerad mot Skatteverkets egen exempelfil) | Implemented | Endast filgenerering — fortfarande ingen API-inlämning till Skatteverket, arbetsgivaren laddar själv upp den genererade filen via Skatteverkets e-tjänst; inget stöd för rättelse-/`Borttag`-inlämning ännu | Om äkta e-inlämning blir ett mål, integrera Skatteverkets AGI-inlämnings-API direkt och lägg till `Borttag`-inlämningsstöd för rättelser | `test_agi_xml_download_produces_valid_structure`, `test_agi_xml_download_blocked_when_company_phone_missing`; genererad utdata validerad mot Skatteverkets `arbetsgivardeklaration_1.1.xsd`/`_component_1.1.xsd` | Rapporterad lönekörning kan inte ändras; en riktig, schemagiltig AGI-fil kan genereras och laddas ned per lönekörning | P1 |
| R-011 | Åtkomstkontroll och ansvarsfördelning | Känsliga åtgärder kräver korrekt behörighet och skälsloggning | Åtgärds-rollmatrisen i `bookkeeping/compliance_policy.py` täcker nu `company.delete` (var tidigare bara en ad hoc-kontroll av `is_superuser`/medlemskap, i strid med `docs/compliance/role-matrix.md` som redan dokumenterade den som en styrd åtgärd) och den nya `voucher_series.manage`; båda kopplade via `@require_compliance_action` | Partial | `company.delete` är styrd till `finance_admin` i stället för `system_admin` som ursprungligen dokumenterat — en medveten, diskuterad ändring så att vanliga administratörer fortfarande kan ta bort ett tomt/felskapat företag utan åtkomst på infrastrukturnivå; `docs/compliance/role-matrix.md` uppdaterad att matcha. De flesta andra känsliga endpoints använder fortfarande ad hoc-kontroller enligt färdplanen i det dokumentet | Fortsätt rollmatrisutrullningen: ersätt kvarvarande ad hoc-kontroller av `is_superuser`/`is_staff` med `@require_compliance_action`-dekoratorer en endpoint i taget | Tester uppdaterade för den nya behörighetsspärren (`test_company_delete_forbidden_for_non_member` med flera ger nu `finance_admin` explicit i stället för att förlita sig på enbart medlemskap) | Användare som inte är finance_admin kan inte radera ett företag; rollmatrisdokumentet matchar koden | P1 |
| R-012 | Backup- och återställningstillförlitlighet | Data kan återställas inom RTO/RPO och bevisas genom test | `compliance_restore_dry_run` stödjer SQLite och PostgreSQL (dump/återställning till en isolerad engångsdatabas, verifierat från början till slut mot `postgres:17-alpine`); schemalagd månadsvis via Ofelia (`docker-compose.yml`) med körning inuti `web`-containern, evidens persisterad till volymen `saldovibe-data` | Implemented | Restore-dry-runnen fungerade tidigare bara mot SQLite och var aldrig faktiskt schemalagd (evidensen visade en enda manuell körning); en färsk PostgreSQL-driftsättning kunde inte ens slutföra `migrate` på grund av en orelaterad transaktionsgränsbugg i `banking/migrations/0004_single_tax_account_per_company.py`, nu åtgärdad med `atomic = False` | Pinna `postgresql-client` till serverns major-version i runtime-imagen; schemalägg det befintliga dry-run-skriptet med Ofelia i stället för att lämna det oschemalagt | ej tillämpligt — implementerat; lägg till ett CI-jobb som verifierar att månadsschema-labeln finns, som regressionsvakt | Månatligt återställningstest producerar signerad rapportartefakt och körs obevakat utan operatörsåtgärd | P0 |
| R-013 | Dataportabilitet för revision | Revisorn kan få ett sammanhängande paket (huvudbok, verifikationer, underlagsindex, auditlogg) | `bookkeeping/export_bundle.py::generate_export_bundle` + `bookkeeping:export_bundle`/`bookkeeping:export_bundle_download` (skyddade med `@require_compliance_action("export.bundle")`, finance_admin) bygger en självständig zip för valfritt användarvalt datumintervall: `bokforingsdata/` (komplett SIE4 per överlappande räkenskapsår), `kontospecifikationer/` (en PDF per konto med ingående/utgående balans och löpande bokningar), `bilagor/` (källbilagor uppdelade bokförda/ej bokförda), `banktransaktioner/` (en CSV per bank-/skattekontokälla med bankens egen bild för perioden, bokförd eller ej), `rapporter/` (omgenererade PDF:er för moms/AGI/lönebesked — men bara perioder som faktiskt är stängda/rapporterade: en PDF per `VatCloseSnapshot`, per `PayrollRun` som redan är `is_reported_to_skatteverket`, och per `SalaryRecord` på en avslutad lönekörning — en pågående/orapporterad period utesluts i stället för att beräknas i farten, så paketet presenterar aldrig oinlämnade siffror som inlämnade), sammanbundet av en bläddringsbar `index.html`-sajt, plus en `manifest.json` med SHA256-checksumma per fil enligt `docs/compliance/audit-export-spec.md` | Implemented | Fas 1+2+3 klara; paketet innefattar fortfarande inte hashkedjeverifieringsresultatet eller SIE/SI-importdiagnostiken så som den ursprungliga E4-skissen i detta dokument tänkte sig | Överväg att lägga till ett `audit/`-avsnitt med hashkedjeverifieringsresultatet och SIE/SI-importdiagnostiken | `bookkeeping.tests.test_export_bundle` (räkenskapsårsöverlapp, kontospecifikationens aritmetik för ingående/löpande/utgående balans, dokumentbucketering över de fyra bilagelänktyperna, banktransaktioners CSV-innehåll och bokförd/ej bokförd-antal, `ReportsSectionTests` för moms/AGI/lönebeskeds periodurvalsregler, finance_admin-behörighetsspärr, zipstruktur + manifesthashverifiering) | En finance_admin kan för valfri period generera ett enda paket med checksummor och manifest som täcker huvudbok, kontospecifikationer, bilagor, banktransaktioner och rapporter (moms/AGI/lönebesked för redan stängda perioder) | P1 |
| R-014 | Övervakning och avvikelsedetektering | Compliance-känsliga avvikelser är synliga | `bookkeeping:compliance_dashboard` visar verifikationsnummerluckor per serie, sena bokföringar (>7 dagar), bokförda leverantörsfakturor utan bilaga, föräldralösa bilagor, låsrelaterade audithändelser (30 d), hashkedjeverifiering per företag, det senaste externa RFC 3161-ankaret samt (nytt) momsstängningssnapshots vars källfingeravtryck inte längre stämmer (R-008-drift) | Implemented | Inget — push-larm formellt avböjt 2026-08-24: en e-post-/notispipeline bryter den integrationsfria linjen; avvikelser lyfts på compliance-dashboarden (finance_admin-styrd) och det månatliga schemalagda evidensjobbet fallerar högljutt om kedjeverifiering eller återställning brister | — | `ComplianceDashboardVatDriftTests`; KPI-frågetester för de övriga indikatorerna saknas fortfarande | Dashboarden exponerar åtgärdbara avvikelser; röda indikatorer når också en operatör utan att sidan öppnas | P2 |
| R-015 | E-fakturering till offentlig sektor (SFS 2018:1277) | Fakturor till svenska offentliga köpare över tröskeln använder ett strukturerat e-fakturaformat | `invoicing/peppol.py` genererar en Peppol BIS Billing 3.0-XML (UBL, EN 16931) per faktura, nedladdningsknapp på fakturadetaljsidan; validerad mot den riktiga Peppol/EN16931-exempelstrukturen och korskontrollerad fält för fält mot den officiella dokumentationen | Implemented | Inget — nätverksleverans (steg 2) formellt avböjd 2026-08-24 enligt den integrationsfria linjen; arbetsflödet med manuell uppladdning är dokumenterat i användarhandboken (`docs/user-guide/07-kundfakturor.md`, "E-faktura (Peppol)") | — | Manuell verifiering mot Peppol BIS Billing 3.0:s struktur-/fältregler (omvänd skattskyldighet, kreditfakturor, momskategorier, obligatorisk köparreferens); lade till `Company.country_code`/`Customer.country_code` (saknades tidigare, obligatoriska för EN 16931) | En schemakonform Peppol-faktura-XML kan genereras och laddas ned per faktura, med tydliga svenska valideringsfel när obligatoriska uppgifter (momsnummer, köparreferens, adresser) saknas | P2 |
| R-017 | Anläggningsregistrets fullständighet (BFNAR 2013:2 punkt 4.5–4.7) | Anläggningsregistret registrerar nedskrivningar, korrigeringar (av redan bokförd avskrivning/nedskrivning), omklassificeringar (mellan tillgångstyper) och utrangering/avyttring, var och en med dokumenterat skäl och, där händelsen påverkar värdet, en balanserad bokning — inte bara anskaffning och linjär avskrivning | `fixed_assets/models.py`: `FixedAssetType` fick `impairment_expense_account`/`accumulated_impairment_account` (seedade per typ i `ensure_default_asset_types` från BAS 7710-7733/1018-1298-mappningar); ny `FixedAssetImpairment`-modell + `FixedAsset.register_impairment()`/`register_impairment_correction()` bokför en riktig balanserad transaktion och kräver skäl; `FixedAssetDepreciation` fick `is_correction`/`correction_of`/`reason` + `FixedAsset.register_depreciation_correction()` för tecknade justeringar av en redan bokförd månad utan att röra originalposten; ny `FixedAssetReclassification`-modell registreras från `asset_update` närhelst `asset_type` ändras; `FixedAsset` fick `disposed_at`/`disposal_type`/`disposal_reason` + `dispose()`; `asset_delete` blockerar nu hård radering när avskrivnings- eller nedskrivningshistorik finns (tidigare alltid hård radering med förkastad historik — raka motsatsen till ett register) | Implemented | Uppskrivningar (BFNAR punkt 4.6) medvetet uppskjutna — skulle kräva `Company.legal_form` och ett tillgångsvärdeskontobegrepp på `FixedAssetType` som inget annat i kodbasen behöver ännu; `fixed_assets`-modellerna (typ, tillgång, avskrivning, nedskrivning, omklassificering) är nu registrerade i `auditlog.TRACKED_MODELS`, vilket stänger behandlingshistorikgapet som noterades här tidigare | Lägg till uppskrivningsstöd om/när ett företag faktiskt behöver skriva upp en tillgång | `test_register_impairment_reduces_book_value_and_posts_balanced_transaction`, `test_register_impairment_requires_reason`, `test_register_impairment_cannot_exceed_book_value`, `test_register_depreciation_correction_adjusts_total_without_double_counting_schedule`, `test_register_depreciation_correction_rejects_zero_amount`, `test_dispose_blocks_further_depreciation_and_double_disposal`, `test_asset_cannot_be_deleted_when_depreciations_exist`, `test_asset_with_history_can_be_disposed_instead`, `test_asset_type_change_is_recorded_as_reclassification`; verifierat från början till slut med riktiga bokningar (nedskrivning, tecknad korrigering, avyttringsblockering) och riktiga huvudbokssaldokontroller per konto | En tillgång med bokförd avskrivnings- eller nedskrivningshistorik kan inte hårdraderas; nedskrivning, korrigering, omklassificering och avyttring är alla fullvärdiga, motiverade och (där de påverkar värdet) bokförda händelser i registret | P1 |
| R-016 | Systemdokumentation och behandlingshistorik (BFNAR 2013:2 kap. 9) | Företaget upprätthåller systemdokumentation som beskriver kontoplan, samlingsplan, arkivplan, verifikationsserieindelning, verifieringskedjor, behandlingsregler, informationsflöden och behandlingshistorik | Ny sida `bookkeeping:system_documentation` och PDF-export (`_build_system_documentation_context`, `templates/bookkeeping/system_documentation*.html`) sammanställer automatiskt alla tio BFN kap. 9-avsnitt per företag: kontoplan och verifikationsserie-per-källa-mappningen (med senast-ändrad-tidsstämpel/aktör hämtade från auditloggen) genereras live från företagets faktiska data; resten är en kurerad, korrekt beskrivning av SaldoVibes egen arkitektur och automatiska behandlingsregler | Implemented | Statiska avsnitt (samlingsplan, behandlingsregler, informationsflöden) beskriver programvaran vid skrivande stund och behöver manuell granskning om arkitekturen ändras väsentligt; inget kassaregisteravsnitt eftersom SaldoVibe saknar kassaregisterfunktion (korrekt utanför omfattningen enligt punkt 9.13) | Genomläsning av BFNAR 2013:2 mot den faktiska kodbasen (inte antagen) lyfte detta som ett helt saknat krav, plus en regression samma dag: `bookkeeping.voucherseriesrule` låg inte i `auditlog.TRACKED_MODELS` (åtgärdat, se R-003), vilket detta dokument beror på för att visa "senast ändrad" | `test_system_documentation_page_includes_all_bfn_kap9_sections`, `test_system_documentation_shows_last_change_to_voucher_series_rule`, `test_system_documentation_pdf_downloads`; verifierat från början till slut mot riktig företagsdata (renderad HTML + en riktig, giltig 2-sidig PDF) | Ett företag kan generera och ladda ned ett systemdokumentationsdokument som täcker varje krav i BFNAR 2013:2 kap. 9, där de föränderliga delarna (kontoplan, verifikationsserier) alltid är aktuella | P2 |

## 2) Fasindelad backlogg (utförandeordning)

## Fas 0: Säkerhetsfrysning (1–2 sprintar)
Mål: ta bort juridiska/compliance-röda flaggor omedelbart.

1. Inaktivera destruktiva raderingsvägar för bokföringsartefakter.
- Blockera transaktionsradering i radering av räkenskapsår/företag.
- Ersätt med arkiverings-/avaktiveringsflöden.
- Filer: `bookkeeping/views/transactions.py`, tillhörande mallar.

2. Lägg till bokförd/oföränderlig-flaggor och skyddsräcken på transaktioner.
- Fält: `Transaction.is_posted`, `posted_at`, `posted_by`.
- Blockera uppdatering/radering när bokförd.
- Filer: `bookkeeping/models.py`, `bookkeeping/views/transactions.py`, formulär/admin.

3. Mjukradering och legal hold för bilagor.
- Fält: `deleted_at`, `deleted_by`, `delete_reason`, `legal_hold`.
- Gör om raderingsendpointen till mjukradering.
- Filer: `attachments/models.py`, `attachments/views.py`.

4. Upprätthållande av periodlås.
- Lägg till `PeriodLock` och upprätthåll i alla boknings-/importflöden.
- Filer: ny modell i `bookkeeping/models.py` eller dedikerad app + kontroller i bokningsvyerna.

Definition av klar för fas 0:
- Ingen normal användarväg kan hårdradera bokförda transaktioner eller underlag.
- Stängda perioder avvisar bokföring/import med tydlig återkoppling till användaren.

## Fas 1: Integritet och evidens (1–2 sprintar)
Mål: göra posterna manipulationsspårbara och rättelsesäkra.

1. Verifikationsnumreringsmotor och unikhetsvillkor.
- Lägg till verifikationsseriemodeller och atomär sekvensallokerare.
- Migrera äldre referenser till strukturerade serie-/nummerfält.

2. Omförings-/rättelseflöde.
- Lägg till rättelselänkning och UI-/API-flöde.

3. Audithashkedja.
- Lägg till `prev_hash`, `entry_hash` i auditmodellen och ett verifierarkommando.

4. Evidenspaket för momsstängning.
- Persistera periodsnapshot med hash av transaktionsmängden.

Definition av klar för fas 1:
- Rättelser är additiva, inte destruktiva.
- Hashverifieringskommandot rapporterar integritetstillstånd.

## Fas 2: Interoperabilitet och rapporteringshärdning (1 sprint)
Mål: stärka arbetsflödena mot extern redovisningskonsult och inlämning.

1. SIE4-export (hela huvudboken).
2. Förbättrad SIE-importvalidering och felrapportering.
3. SRU-diagnostikrapport och striktare validering före export.
4. AGI-evidenspaket för lön och låsbeteende efter rapportering.

Definition av klar för fas 2:
- En redovisningskonsult kan exportera ett komplett periodpaket med förutsägbar valideringsutdata.

## Fas 3: Drift och styrning (löpande)
Mål: bevisa kontinuerlig efterlevnadsstatus.

1. Backup- och återställningsrunbook + automatiserat månatligt återställningstest.
2. Rollmatris och policy-som-kod för högriskåtgärder.
3. Compliance-dashboard och avvikelselarm.
4. Checklista för kvartalsvis intern compliance-genomgång.

Definition av klar för fas 3:
- Återställningstester och compliance-kontroller producerar återkommande evidensartefakter.

### Implementationsstatus fas 3 (aktuell)

Implementerat:
- Kommando för backup-/återställningstest: `manage.py compliance_restore_dry_run` — stödjer nu
  både SQLite (dev) och PostgreSQL (prod: dump → återställning till engångsdatabas → verifiering
  → borttagning), pinnat till matchande `postgresql-client-17` i runtime-imagen så att det
  faktiskt fungerar mot `postgres:17-alpine`. Åtgärdade en orelaterad migrationsbugg
  (`banking/migrations/0004_*`, `atomic = False`) som tidigare hindrade `migrate` från att
  slutföras på en färsk Postgres-databas.
- Månatligt driftskript: `scripts/monthly_restore_dry_run.sh`, som nu även kör
  `verify_audit_chain_anchors` och `anchor_audit_chain` (se R-004). Faktiskt schemalagt — en
  dedikerad Ofelia-`scheduler`-tjänst i `docker-compose.yml` kör det månadsvis inuti
  `web`-containern; tidigare fanns skriptet men inget anropade det automatiskt.
- Extern RFC 3161-förankring av auditkedjan: `auditlog/timestamping.py`,
  `AuditChainAnchor`-modellen, kommandona `anchor_audit_chain`/`verify_audit_chain_anchors`
  (se R-004 för manipulera-och-omförsegla-beviset).
- Compliance-dashboardens endpoint och UI: `bookkeeping:compliance_dashboard`, som nu även
  visar tidsstämpeln för det senaste externa kedjeankaret.
- Verifikationsserier är nu faktiskt användbara per företag: `VoucherSeriesRule` +
  inställnings-UI på `bookkeeping:voucher_series_settings` (se R-003).
- Riktig Peppol BIS Billing 3.0-e-faktura-XML-generering (se R-015) och en riktig
  AGI-XML-generator (arbetsgivardeklaration på individnivå) validerad mot Skatteverkets
  publicerade XSD-scheman (se R-010).
- SIE4-exporten kompletterad med `#IB`/`#UB`/`#RES`/`#SRU`; SIE-importen namnger nu
  autoskapade konton från filens `#KONTO` och stämmer av `#UB` mot huvudbokssaldot efter
  import (se R-007).
- SRU-koder autotilldelas vid kontoskapande (se R-009).
- Bilagetillägg/-borttagning fungerar nu över alla tre dokumenttyper som kan bära underlag —
  leverantörsfakturor, kundfakturor och manuella verifikationer — även redan bokförda,
  styrt enbart av periodlås i stället för den tidigare `legal_hold`-vid-bokföring-flaggan,
  som togs bort (se R-005).
- Anläggningsregistret registrerar nu nedskrivningar, korrigeringar, omklassificeringar och
  utrangering/avyttring, var och en motiverad och (där värdet påverkas) bokförd som en riktig
  balanserad transaktion; hård radering av en tillgång med avskrivnings-/nedskrivningshistorik
  blockeras nu till förmån för `dispose()` (se R-017).
- Auditloggtäckningen (behandlingshistoriken) utökad till `payroll` (Employee — personnummer
  maskerat, PayrollRun, SalaryRecord, SalaryAdjustment), `vat.vatclosesnapshot`,
  `attachments.transactionattachment` och alla fem `fixed_assets`-modellerna — tidigare
  spårades bara bookkeeping/banking/invoicing/supplier_invoices/expenses, så löne-,
  momsstängnings- och bilagehändelser var osynliga i auditkedjan.
- Oföränderlighetstriggrar på databasnivå (SQLite + Postgres) på transaktioner i låsta
  perioder och på auditloggrader, vilket stänger admin-/queryset-kringgåendet runt den
  signalbaserade auditloggningen (se R-001).
- Driftdetektering av momsstängningssnapshots på compliance-dashboarden och en
  periodlåsuppmaning vid stängning av en olåst momsperiod (se R-008).
- SIE-omimportens ersättning nu begränsad till transaktioner importen själv skapade
  (`source=sie_import`) — nativt bokförda verifikationer kan inte längre raderas av någon
  import (se R-001).
- Compliance-dokumentationspaket under `docs/compliance/`
	- `skatteverket-controls.md`
	- `restore-runbook.md` (uppdaterad med Postgres- och kedjeankarprocedurerna)
	- `audit-export-spec.md`
	- `role-matrix.md` (uppdaterad: `company.delete` → `finance_admin`, tillagd `voucher_series.manage`)
	- `quarterly-review-checklist.md`
- Rollmatrisutrullningen slutförd (2026-08-24): varje åtgärdsnyckel med en HTTP-endpoint är
  skyddad av `require_compliance_action` — inklusive de tidigare oskyddade momsstängningen/
  eSKD-exporten (`vat.close_period`), SIE/SI-importen (`import.sie`) och compliance-dashboarden
  (`audit.verify_chain`, eftersom den verifierar kedjan inline) — var och en med ett
  blockerad-för-icke-admin-test. `restore.dry_run` och `gdpr.erase` är rena
  shell-management-kommandon, upprätthållna av deployåtkomst; kvarvarande
  `is_staff`/`is_superuser`-kontroller är tenant-medlemskapsavgränsning, inte roller. Se
  `docs/compliance/role-matrix.md`.

Beslutat bort (2026-08-24), enligt den integrationsfria linjen — kommer inte att byggas:
- E-fakturering steg 2 (leverans till en Peppol-accesspunkt): arbetsflödet med manuell
  uppladdning är dokumenterat i användarhandboken i stället (se R-015).
- Push-larm för compliance-avvikelser (e-post-/notispipeline): compliance-dashboarden plus
  det månatliga schemalagda evidensjobbet täcker R-014.

Pågående:
- GDPR/dataskyddsförordningen: spåras separat i `GDPR_COMPLIANCE_PLAN.md` — faserna 0–2
  KLARA 2026-08-15, gallringskoden medvetet uppskjuten till ~2032 enligt dess fas 3.
- Uppskrivningar (BFNAR punkt 4.6) medvetet uppskjutna — skulle kräva ett
  tillgångsvärdeskontobegrepp som inget annat behöver ännu (se R-017; `Company.legal_form`
  finns sedan bokslutsflödet, så bara kontobegreppet återstår).
- Bekräfta den första *obevakade* månatliga restore-dry-runnen efter 2026-09-01 03:00
  (Ofelia-schemaläggaren deployades till prod först 2026-08-12, efter augustis fönster; en
  manuell körning 2026-08-14 producerade augustis evidens i
  `/data/compliance-evidence/restore-tests/`), och kör den första kvartalsgenomgången enligt
  `docs/compliance/quarterly-review-checklist.md`.

## 3) Föreslagen arbetsnedbrytning (initiala ärenden)

### Epic A: Oföränderlighet och raderingskontroller
- A1: Blockera radering av bokförda transaktioner i vyer/tjänster.
- A2: Migrera företags-/årsradering till icke-destruktiv arkivering.
- A3: Lägg till omföringsendpoint och UI-åtgärd från verifikationsdetaljen.
- A4: Lägg till regressionstester för blockerade raderingar och omföring.

### Epic B: Periodlås
- B1: Skapa periodlåsmodell + admin. Klart.
- B2: Upprätthåll lås vid manuellt transaktionstillägg. Klart.
- B3: Upprätthåll lås vid SI-/SIE-import. Klart.
- B4: Upprätthåll lås vid leverantörs-/lönebokföring. Klart.
- B5: Användarvänt UI för att låsa ett helt räkenskapsår eller en delperiod, med skälskrävande återöppning/omlåsning och auditloggning. Klart (`bookkeeping:period_lock_list`).

### Epic C: Bevarande av underlag
- C1: Schemamigrering för mjukradering av bilagor.
- C2: Ersätt raderingsendpointens beteende.
- C3: Valfritt legal hold-flöde.
- C4: Tester för bilagebevarande och återställning.

### Epic D: Numrering och auditintegritet
- D1: Strukturerad verifikationsnummermodell.
- D2: Backfyllningsskript för befintliga referenser.
- D3: Migrering av audithashkedjan och skrivarlogik.
- D4: Management-kommando för integritetsverifiering.

### Epic E: Exporthärdning
- E1: SIE4-exportkommando/-endpoint.
- E2: SRU-förhandsvalidering.
- E3: Evidens för momsstängningssnapshot.
- E4: Samlat revisionsexportpaket — fas 1+2+3 implementerade (`bookkeeping/export_bundle.py`,
  `bookkeeping:export_bundle`): huvudbok (SIE4) + kontospecifikationer + bilagor +
  banktransaktioner + rapporter (moms/AGI/lönebesked, endast för redan stängda/rapporterade
  perioder), med manifest.json/SHA256-checksummor, styrt till finance_admin (se R-013).

## 4) QA-strategi för compliance-kritiska vägar

1. Tester för oföränderlig huvudbok.
- Försök till uppdatering/radering av bokförd verifikation → måste misslyckas.

2. Tester för stängda perioder.
- Försök till bokföring/import i stängd period → måste misslyckas.

3. Korrekthetstester för omföring.
- Omföringen måste nolla originalverifikationen med båda posterna bevarade.

4. Tester för evidensbevarande.
- Mjukraderad bilaga förblir återfinnbar i auditkontexten.

5. Reproducerbarhetstester för export.
- Samma indataperiod → deterministisk utdata plus stabila checksummor där det förväntas.

## 5) Föreslagna repo-artefakter att lägga till härnäst

1. `docs/compliance/skatteverket-controls.md`
- Kontrollkatalog, ansvarig, frekvens, evidenskälla.

2. `docs/compliance/restore-runbook.md`
- Operativa steg för backup/återställning och månatlig checklista.

3. `docs/compliance/audit-export-spec.md`
- Manifestschema, checksumstrategi, definition av paketinnehåll.

4. `docs/compliance/role-matrix.md`
- Känsliga åtgärder och krävd roll/godkännande.

## 6) Rekommendation för första sprinten

Implementera fas 0 i denna ordning:
1. Blockera hård radering av bokförda transaktioner och ersätt med arkiveringsbeteende.
2. Lägg till periodlåskontroller i transaktionsskapande + SI-/SIE-importer.
3. Gör om bilageradering till mjukradering.
4. Lägg till tester som bevisar dessa kontroller.
