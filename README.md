# SaldoVibe

SaldoVibe är en Django-baserad bokföringsapp för svenska företag (svenskt gränssnitt, BAS-kontoplan, SIE-import/export, efterlevnad av Bokföringslagen och Skatteverkets krav). Repot innehåller både en produktionslik Docker-uppsättning och en lättviktig utvecklingscontainer.

Appen är utvecklad för att uppfylla mina egna behov av bokföring, inte för att konkurrera med kommersiella tjänster. Det präglar designen:

- **Självhostad och kostnadsfri i drift.** Integrationer undviks där de skapar en löpande kostnad till andra leverantörer. OCR-tolkningen av uppladdade bilagor körs t.ex. lokalt i `web`-containern i stället för via en betald molntjänst, och den externa tidsstämplingen av revisionskedjan använder en fri RFC 3161-tjänst.
- **Efterlevnad utan mellanhänder.** Periodlåsning, hash-kedjad och oföränderlig verifikationslogg, SIE-export och AGI-rapportering mot Skatteverkets API är inbyggda, så att kraven i Bokföringslagen och BFNAR 2013:2 uppfylls direkt av appen.
- **Enkel teknikstack.** Serverrenderade Django-mallar utan JS-byggsteg, sqlite för utveckling och Postgres i produktion — allt data stannar på din egen server.
- **Enkel funktionalitet och rena flöden.** SaldoVibe ska inte vara mer komplicerat än nödvändigt. Appen riktar sig till dig som redan kan bokföring och vill göra den själv — den hjälper till med struktur, kontroller och efterlevnad, men försöker inte automatisera bort bokföringskunskapen.

För hur appen används som slutanvändare, se [användarhandboken](docs/user-guide/README.md).

## Status och buggrapportering

Testningen är inte komplett — appen används i verklig bokföring, men räkna med att det finns buggar, särskilt i mindre använda flöden. Granska alltid genererade rapporter och deklarationsunderlag innan de lämnas in.

Buggar rapporteras som [issues på GitHub](https://github.com/montegrotto/SaldoVibe/issues). Beskriv gärna vilket flöde du var i, vad du förväntade dig och vad som hände i stället. Sårbarheter rapporteras privat enligt [SECURITY.md](SECURITY.md).

## Snabbstart med Docker Hub

Färdigbyggda imager (amd64 och arm64) publiceras på Docker Hub som
[`montegrotto/saldovibe`](https://hub.docker.com/r/montegrotto/saldovibe). Den fristående
compose-filen kör imagen med sqlite på en volym, utan Postgres eller nginx:

```bash
cp .env.example .env     # sätt SALDOVIBE_PUBLIC_URL till adressen du kommer att surfa till
docker compose up -d     # hämtar montegrotto/saldovibe:latest, migrerar databasen, lyssnar på :8000
```

Första användaren skapas i appens eget registreringsflöde
([användarhandboken, kapitel 1](docs/user-guide/01-komma-igang.md)). Pinna en version med
`SALDOVIBE_VERSION=1.0.0` i `.env`. För Postgres och nginx, se "Köra i produktion" nedan.

## Containerstruktur

- `Dockerfile` bygger produktionsimagen med Python, Node.js, WhiteNoise och Gunicorn.
- `docker-compose.prod.yml` kör appen med PostgreSQL och nginx.
- `Dockerfile.dev` och `docker-compose.dev.yml` kör en lokal utvecklingscontainer mot sqlite.

## Köra i produktion

Skapa en produktionsmiljöfil från exemplet och sätt riktiga värden innan stacken startas:

```bash
cp .env.prod.example .env.prod
```

Starta produktionsstacken med den publicerade imagen, eller bygg den själv:

```bash
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml up --build -d   # bygger imagen lokalt från repot
```

Detta startar fyra tjänster:

- `web` för Django + Gunicorn — inklusive lokal OCR-fältföreslagning för uppladdade bilagor (se `attachments/extraction_client.py`), ingen separat tjänst behövs
- `db` för PostgreSQL
- `nginx` som publik ingång för `/`, `/static/` och `/media/`
- `scheduler` — [ofelia](https://github.com/mcuadros/ofelia), som kör den månatliga återställnings-testkörningen och e-postbilagehämtningen var 15:e minut som cron-jobb inuti `web`

Alla inloggningsuppgifter ligger i `.env.prod`, inte i compose-filen. Exempelvärdena är platshållare endast för lokalt bruk — byt ut dem innan du driftsätter någonstans på riktigt.

## Köra i utveckling

Skapa en utvecklingsmiljöfil från exemplet:

```bash
cp .env.dev.example .env.dev
```

Bygg och starta utvecklingsstacken:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Detta monterar repot in i containern, använder sqlite och startar Django med `runserver` för snabb iteration.

## Utvecklingskontroller

Lint/formatering upprätthålls med [ruff](https://docs.astral.sh/ruff/) (konfiguration i `pyproject.toml`) och testsviten körs med Djangos testkörare:

```bash
pip install -r requirements-dev.lock
ruff check .
ruff format --check .
python scripts/check_dockerfile_apps.py     # Dockerfile måste COPY:a varje Django-app
python scripts/check_requirements_lock.py
npm ci                                       # vendorerade frontend-tillgångar, krävs av collectstatic
python manage.py collectstatic --noinput     # testerna körs med DEBUG=False och behöver manifestet
python manage.py test
```

GitHub Actions kör samma kontroller vid varje push och pull request (`.github/workflows/ci.yml`).

## Beroenden

`requirements.txt` och `requirements-dev.txt` listar de direkta beroendena och är filerna du
redigerar. `requirements.lock` och `requirements-dev.lock` genereras från dem med varje transitiv
version pinnad och sha256-hashad, och är vad Docker, CI och din virtualenv installerar — så en given
commit ger alltid samma miljö, och ett manipulerat paket får installationen att misslyckas i stället
för att skeppas. Generera om båda efter varje beroendeändring:

```bash
uv pip compile requirements.txt --generate-hashes --python-version 3.13 \
  --output-file requirements.lock
uv pip compile requirements-dev.txt --generate-hashes --python-version 3.13 \
  --output-file requirements-dev.lock
```

`scripts/check_requirements_lock.py` (körs av CI och pre-commit-hooken) misslyckas om en
requirements-fil och dess lock-fil inte stämmer överens.

För att köra kontrollerna automatiskt via git-hooks (lint vid commit, tester vid push), aktivera de incheckade hookarna en gång per klon:

```bash
git config core.hooksPath .githooks
```

Använd `git push --no-verify` för att hoppa över testkörningen i nödfall.

## Miljövariabler

Varje compose-fil läser sina variabler via `env_file`, så inget utom fasta flaggor i stil med
`DJANGO_DEBUG` ligger inline i YAML-filen:

- `docker-compose.yml` (fristående körning av produktionsimagen utan Postgres/nginx) läser `.env` — kopiera den från `.env.example`.
- `docker-compose.dev.yml` läser `.env.dev` — kopiera den från `.env.dev.example`.
- `docker-compose.prod.yml` läser `.env.prod` — kopiera den från `.env.prod.example`.

Checka bara in `*.example`-filerna; håll de riktiga `.env`, `.env.dev` och `.env.prod` ospårade.

`ALLOWED_HOSTS` och `CSRF_TRUSTED_ORIGINS` sätts inte direkt — sätt i stället `SALDOVIBE_PUBLIC_URL`
(t.ex. `http://localhost:8000`) så härleder Django båda från den. Använd de explicita variablerna
`DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` endast för driftsättningar med flera domäner; se
[docs/ops/environment-variables.md](docs/ops/environment-variables.md) för den fullständiga, samlade
referensen över varje variabel appen läser (Django/nätverk, databas, Skatteverkets API).

Varje compose-fil deklarerar också sitt eget Compose-projektnamn (`name:`: `saldovibe-standalone`,
`saldovibe-dev`; `docker-compose.prod.yml` behåller den implicita standarden) så att deras nätverk och
volymer aldrig krockar om du kör fler än en av stackarna på samma värd.

## Noteringar

- Produktionsimagen serverar statiska filer med WhiteNoise inuti Django, medan nginx serverar de delade static- och media-monteringarna framför appen.
- Datavolymen är separat från imagen, så sqlite och uppladdningar överlever omstarter.

## Drift

För backup/återställning i produktion, driftsättningssteg, migreringar och loggning/övervakning, se [docs/ops/](docs/ops/README.md).

## Licens

MIT, se [LICENSE](LICENSE). BAS-kontoplanen och annat tredjepartsmaterial i repot har egna villkor,
se [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Bidrag: [CONTRIBUTING.md](CONTRIBUTING.md).
