# SaldoVibe

**Webbplats och användarhandbok: [www.saldovibe.se](http://www.saldovibe.se/)**

SaldoVibe är en Django-baserad bokföringsapp för svenska företag (svenskt gränssnitt, BAS-kontoplan, SIE-import/export, efterlevnad av Bokföringslagen och Skatteverkets krav). Repot innehåller en komplett Docker-uppsättning för drift; utveckling sker direkt mot en lokal virtualenv.

Appen är utvecklad för att uppfylla mina egna behov av bokföring, inte för att konkurrera med kommersiella tjänster. Det präglar designen:

- **Självhostad och kostnadsfri i drift.** Integrationer undviks där de skapar en löpande kostnad till andra leverantörer. OCR-tolkningen av uppladdade bilagor körs t.ex. lokalt i `web`-containern i stället för via en betald molntjänst, och den externa tidsstämplingen av revisionskedjan använder en fri RFC 3161-tjänst.
- **Efterlevnad utan mellanhänder.** Periodlåsning, hash-kedjad och oföränderlig verifikationslogg, SIE-export och AGI-rapportering mot Skatteverkets API är inbyggda, så att kraven i Bokföringslagen och BFNAR 2013:2 uppfylls direkt av appen.
- **Enkel teknikstack.** Serverrenderade Django-mallar utan JS-byggsteg, sqlite för utveckling och Postgres i produktion — allt data stannar på din egen server.
- **Enkel funktionalitet och rena flöden.** SaldoVibe ska inte vara mer komplicerat än nödvändigt. Appen riktar sig till dig som redan kan bokföring och vill göra den själv — den hjälper till med struktur, kontroller och efterlevnad, men försöker inte automatisera bort bokföringskunskapen.

För hur appen används som slutanvändare, se [användarhandboken](docs/user-guide/README.md).

## Status och buggrapportering

Testningen är inte komplett — appen används i verklig bokföring, men räkna med att det finns buggar, särskilt i mindre använda flöden. Granska alltid genererade rapporter och deklarationsunderlag innan de lämnas in.

Buggar rapporteras som [issues på GitHub](https://github.com/montegrotto/SaldoVibe/issues). Beskriv gärna vilket flöde du var i, vad du förväntade dig och vad som hände i stället. Sårbarheter rapporteras privat enligt [SECURITY.md](SECURITY.md).

## Köra i produktion

Färdigbyggda imager (amd64 och arm64) publiceras på Docker Hub som
[`montegrotto/saldovibe`](https://hub.docker.com/r/montegrotto/saldovibe).
`Dockerfile` bygger produktionsimagen med Python, Node.js, WhiteNoise och Gunicorn;
`docker-compose.yml` kör appen med PostgreSQL och nginx.

Servern behöver bara `docker-compose.yml` och `.env` — ingen utcheckning (nginx-konfigen ligger inline i compose-filen). Skapa miljöfilen från exemplet och sätt riktiga värden innan stacken startas:

```bash
cp .env.example .env
```

Starta produktionsstacken med den publicerade imagen, eller bygg den själv:

```bash
docker compose pull && docker compose up -d
docker compose up --build -d   # bygger imagen lokalt från repot
```

Första användaren skapas i appens eget registreringsflöde
([användarhandboken, kapitel 1](docs/user-guide/01-komma-igang.md)). Pinna en version med
`SALDOVIBE_VERSION=1.0.0` i `.env`.

Detta startar fyra tjänster:

- `web` för Django + Gunicorn — inklusive lokal OCR-fältföreslagning för uppladdade bilagor (se `attachments/extraction_client.py`), ingen separat tjänst behövs
- `db` för PostgreSQL
- `nginx` som publik ingång för `/`, `/static/` och `/media/`
- `scheduler` — [ofelia](https://github.com/mcuadros/ofelia), som kör den månatliga återställnings-testkörningen och e-postbilagehämtningen var 15:e minut som cron-jobb inuti `web`

Alla inloggningsuppgifter ligger i `.env`, inte i compose-filen. Exempelvärdena är platshållare endast för lokalt bruk — byt ut dem innan du driftsätter någonstans på riktigt.

## Köra i utveckling

Utveckling sker utan Docker, direkt mot sqlite:

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.lock
cp .env.example .env     # avkommentera DJANGO_DEBUG=1
python manage.py runserver
```

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

All konfiguration bor i `.env` (kopiera från `.env.example`) — compose-filen läser den via
`env_file`, en bar `manage.py runserver` via python-dotenv, så inget utom fasta flaggor i stil med
`DJANGO_DEBUG` ligger inline i YAML-filen. Checka bara in `.env.example`; håll den riktiga `.env`
ospårad.

`ALLOWED_HOSTS` och `CSRF_TRUSTED_ORIGINS` sätts inte direkt — sätt i stället `SALDOVIBE_PUBLIC_URL`
(t.ex. `http://localhost:8000`) så härleder Django båda från den. Använd de explicita variablerna
`DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` endast för driftsättningar med flera domäner; se
[docs/ops/environment-variables.md](docs/ops/environment-variables.md) för den fullständiga, samlade
referensen över varje variabel appen läser (Django/nätverk, databas, Skatteverkets API).

## Noteringar

- Produktionsimagen serverar statiska filer med WhiteNoise inuti Django, medan nginx serverar de delade static- och media-monteringarna framför appen.
- Datavolymerna är separata från imagen, så databasen och uppladdningar överlever omstarter.

## Drift

För backup/återställning i produktion, driftsättningssteg, migreringar och loggning/övervakning, se [docs/ops/](docs/ops/README.md).

## Licens

MIT, se [LICENSE](LICENSE). BAS-kontoplanen och annat tredjepartsmaterial i repot har egna villkor,
se [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Bidrag: [CONTRIBUTING.md](CONTRIBUTING.md).
