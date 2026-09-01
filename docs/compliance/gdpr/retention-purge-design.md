# Design för gallring efter bevarandetiden (G-008, art. 5.1 e)

Statusdatum: 2026-08-15
Status: **design beslutad — implementation uppskjuten.** Den äldsta datan i någon
driftsättning är flera år från 7-årsgränsen; detta dokument låser besluten så att inget som
byggs fram till dess gör gallringen omöjlig. Implementationen måste schemaläggas innan den
första driftsättningens äldsta räkenskapsår passerar gränsen (~2032).

## Vad gallringen gör

Förstör ett räkenskapsårs räkenskapsinformation när dess lagstadgade bevarandetid löpt ut:
gallring av räkenskapsår Y tillåts först när `Y.end_date`:s **kalenderår + 7 hela år** har
passerat (BFL 7 kap. 2 §), och endast för företagets **äldsta kvarvarande** räkenskapsår
(aldrig mitt i sekvensen — både ingående balanser och auditkedjan beror på prefixordningen).

Ingår per år, i raderingsordning:

1. Bilagor vars ägande rader tillhör året — DB-rader **och** mediafiler (obs:
   `legal_hold` kan inte överleva den lagstadgade period den skyddar, men en *aktiv*
   legal hold från en pågående tvist blockerar hela årets gallring — kontrollera först,
   avbryt om någon finns).
2. Lön: löneposter/justeringar, lönekörningar, AGI-evidens för året.
3. Moms-snapshots, bankimporter/-transaktioner daterade inom året.
4. Journalposter, därefter årets transaktioner (kräver att triggerfamiljen
   `trg_bookkeeping_transaction_no_delete_locked` tillfälligt släpps — se mekanik).
5. Auditkedjeposter vars nyaste refererade post tillhör året (se återförankring av kedjan
   nedan).
6. Själva `AccountingYear`-raden, efter att gallringsprotokollet skrivits.

Gallras **inte**: stamdata (konton, kunder, leverantörer, anställda) — hanteras av klass
B-triggarna i `retention-schedule.md`; företagsraden; senare års data även när den refererar
gallrade motparter vid namn (de strängarna ligger i de senare raderna).

## Auditkedjeproblemet och återförankring

Kedjan är append-only med DB-triggrar som blockerar UPDATE/DELETE
(`trg_auditlog_auditlogentry_no_*`), och varje post hashar över företagets föregående post —
en kedja per företag via `chain_key`, med posterna från före uppdelningen (`hash_version=1`)
som ett fruset globalt legacy-segment. Det gör gallringssnittet per företagskedja: ett
företag med förlängd bevarandegrund (t.ex. pågående tvist) blockerar inte gallring av
övriga företags kedjor. Att radera
ett *prefix* av kedjan är den enda strukturellt säkra förstörelsen: allt efter snittet
verifierar fortfarande mot sig självt, men den första överlevande postens `prev_hash` pekar
nu på en gallrad post.

Design: en **gallringsmarkörpost** läggs till vid gallringstillfället som registrerar (a)
det gallrade räkenskapsåret, (b) `entry_hash` för den sista gallrade posten (så att
snittpunkten är bevisbar), (c) antal per modell, (d) operatören. Efter prefixraderingen är
den första överlevande postens hängande `prev_hash` förväntad och dokumenterad:
`verify_audit_chain` får en regel — en hängande `prev_hash` är giltig **om och endast om**
en gallringsmarkör intygar exakt den hashen som snittpunkt. Därefter körs
`anchor_audit_chain` omedelbart, så att kedjans tillstånd efter gallringen får en extern
RFC 3161-attestering samma dag. `AuditChainAnchor`-rader som pekar på gallrade poster
överlever (FK är `SET_NULL`) och fortsätter bevisa att historiken före gallringen fanns —
verifieringen av dessa ankare måste tolerera att den refererade posten är borta när en
gallringsmarkör täcker den.

## Mekanik och behörighetsstyrning

- Management-kommando `purge_accounting_year --company <pk> --year <pk> --yes`, med samma
  bekräftelsestil som `gdpr_anonymize`, plus en interaktiv sammanfattning av vad som kommer
  att förstöras (antal per tabell) före körning.
- Compliance-åtgärd `retention.purge` = **system_admin** i
  `bookkeeping/compliance_policy.py` (starkare än `gdpr.erase`: detta förstör
  räkenskapsinformation).
- Kommandot omsluter triggersläpp/-återskapande och raderingarna i en transaktion per år;
  triggrarna återskapas även vid fel (`try/finally`), och gallringsmarkören skrivs *före*
  raderingarna så att en krasch mitt i gallringen lämnar ett över-attesterat, inte
  under-attesterat, tillstånd.
- **Gallringsprotokoll** (förstörelseprotokoll) skrivs till
  `docs/compliance/evidence/purges/` (eller `/data/compliance-evidence/purges` i
  produktion): företag, år, tabellantal, snittpunktshash, tidsstämpel, operatör.
  Protokollet i sig innehåller inga personuppgifter och bevaras på obestämd tid.
- Backuper: gallrad data åldras ut ur backupmängden inom den rullande 90-dagarsgränsen
  (`restore-runbook.md`, G-014). En återställning från en backup tagen före gallringen
  måste köra om gallringen — lägg gallringsmarkörerna i samma återappliceringssteg som
  `gdpr-erasures.jsonl`.

## Begränsningar för kod som skrivs fram till dess

1. Inget får skapa FK:er över årsgränser *in i* transaktions-/bilagerader från utanför
   bokföringsapparna (skulle hänga löst efter gallring). Nya FK:er till `Transaction`,
   `JournalEntry` eller bilagor måste välja `SET_NULL`/`CASCADE` medvetet, med gallringen i
   åtanke.
2. Ändringar i `verify_audit_chain` / `verify_audit_chain_anchors` måste hålla
   gallringsmarkörregeln implementerbar (inget antagande att post-id 1 finns eller att
   `prev_hash=""` bara förekommer vid genesis).
3. Anläggningstillgångar: en tillgång anskaffad år Y som fortfarande skrivs av blockerar
   gallring av Y (dess anskaffningsverifikation är fortfarande "levande"
   räkenskapsinformation för innevarande års avskrivningar). Gallringskontrollen måste
   vägra så länge någon inte färdigavskriven tillgång refererar året.

## Beslutslogg

- **Endast prefixgallring** framför radvis maskering: maskering inuti en hashkedja är
  kryptografiskt omöjlig utan omförsegling, och omförsegling förstör kedjans bevisvärde
  (se `restore-runbook.md` om `reseal_audit_chain`).
- **Gallringsmarkör i kedjan** framför en extern liggare: kedjan är redan det
  manipulationssäkra lagret; en sidofil skulle kunna redigeras.
- **system_admin** framför finance_admin: förstörelse av räkenskapsinformation är en
  åtgärd på infrastrukturnivå; finance_admin behåller `gdpr.erase` (endast klass B).
