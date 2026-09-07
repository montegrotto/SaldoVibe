---
description: "Var SaldoVibes loggar hamnar och vad som bör övervakas i produktion."
---

# Loggning & övervakning

## Var loggarna hamnar

Ingen filbaserad eller extern loggleverans är konfigurerad — allt loggas till **stdout/stderr**
via en enda console-handler (`saldovibe/settings.py`, `LOGGING`), vilket är rätt form för en
container: låt container-runtimen/hosten samla in det.

```bash
docker compose logs -f web
docker compose logs -f db
docker compose logs -f nginx
```

Gunicorn (`web`-processen) startas med `--access-logfile - --error-logfile -` (CMD i
`Dockerfile`), så både HTTP-accessloggar och felloggar hamnar i samma ström som Djangos egen
loggning.

## Loggnivåer och loggers

| Logger | Nivå | Kommentar |
|---|---|---|
| root / `django` | `INFO` | Meddelanden på ramverksnivå. |
| `django.request` | `WARNING` | 4xx/5xx-fel på requests — det är här trasiga vyer syns. |
| `attachments` | `INFO` | Uppladdning/radering/miniatyrer av bilagor. |
| `attachments.email_import` | `DEBUG` i dev, `INFO` i prod | Importkörningar från e-post (Gmail/Outlook) — se [användarhandboken, kapitel 4](../user-guide/04-bilagor.md). |
| `bookkeeping` | `INFO` | Livscykelhändelser för företag/konton/transaktioner (skapande, raderingsförsök, SIE-importresultat). |

De flesta vyer i appen anropar både `messages.error/success/...` för återkoppling till användaren
*och* `logger.info`/`logger.warning`/`logger.exception` för samma händelse — så containerloggarna
är ett rimligt spår av "vad som hände och för vilket företag/vilken användare" även utan en
dedikerad loggaggregator. Strukturerade fält skickas via `extra={...}` (company_id, user_id och
åtgärdsspecifik detalj), vilket fungerar med de flesta loggprocessorer som kan tolka strukturerade
`extra`-fält (t.ex. via `python-json-logger`) om du lägger till en senare.

## Vad som faktiskt bör bevakas i produktion

Eftersom appen inte levereras med någon dashboard eller larmning bör de här ses som manuella
(eller lätt skriptade) kontroller värda att göra regelbundet, utöver generell containerhälsa:

- **WARNING/ERROR-poster från `django.request`** — återkommande 500 på samma vy är det första
  tecknet på att något är trasigt för användarna.
- **Upprepade meddelanden "perioden är låst" / "inte i balans"** i `bookkeeping`-loggarna — kan
  betyda att en användare har fastnat i ett verkligt arbetsflödesproblem, inte bara en
  valideringsvarning.
- **Fel från `attachments.email_import`** — en trasig IMAP/OAuth-inloggning slutar tyst att hämta
  in bilagor; ingenting annat visar det utom loggraden och varningen i appen som visas en gång vid
  konfigurationen.
- **Skatteverket-API-fel vid avslut av lönekörning** — eftersom misslyckade skatteuppslag blockerar
  avslutet av en lönekörning (se [environment-variables.md](environment-variables.md)) betyder en
  topp här att lönerna står stilla för hela företaget tills API:et eller inloggningen är fixad.

För de compliance-specifika signalerna (luckor i verifikationsnumreringen, sena bokningar,
föräldralösa bilagor, avvikelser i revisionsloggens hashkedja) använder du **Compliance-översikten**
i appen ([användarhandboken, kapitel 10](../user-guide/10-rapporter.md)) i stället för att greppa
loggar — den är byggd för exakt det.

## Ingen extern övervakning är kopplad

Det finns för närvarande ingen APM, upptidskontroll eller metrics-exporter i stacken. Om/när det
blir värt att lägga till är de naturliga integrationspunkterna:

- Ett loggbaserat larm på `django.request` (redan tillräckligt strukturerat för att sätta
  tröskelvärden på).
- En HTTP-hälsokontroll mot `/` för upptidsövervakning (nginx proxar redan `/` rakt till `web`;
  det finns ingen dedikerad `/healthz`-endpoint i dag).
- Postgres egna `pg_isready`, som redan används som Compose-healthcheck för `db` — återanvänd den
  kontrollen för extern upptidsövervakning också i stället för att hitta på en ny.
