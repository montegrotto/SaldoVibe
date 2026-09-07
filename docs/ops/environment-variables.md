---
description: "Referens för varje miljövariabel som SaldoVibes Docker-stack läser."
---

# Miljövariabler

Samlad referens för varje miljövariabel som den körande appen läser. Det här är den enda källan —
rotens `README.md` länkar hit i stället för att duplicera listan.

Värdena läses i `saldovibe/settings.py` om inget annat anges.

Docker Compose sätter dem via `env_file: .env` i `docker-compose.yml` (och läser samma fil för
`${...}`-interpolering i YAML:en, t.ex. `NGINX_HTTP_PORT`). Utanför Docker laddar `manage.py`
automatiskt rotens `.env` via `python-dotenv` (utan att skriva över variabler som redan finns i
miljön), så ett vanligt `python manage.py runserver` plockar upp samma fil — förutom där
compose-filen hårdkodar en variabel själv (t.ex. `DJANGO_DEBUG: "0"`), vilket alltid vinner inne i
stacken oavsett `.env`.

## Grundläggande Django / nätverk

| Variabel | Standard | Syfte |
|---|---|---|
| `DJANGO_DEBUG` | `0` | Djangos debug-läge. Måste vara `0` i produktion. |
| `DJANGO_SECRET_KEY` | osäkert publikt reservvärde | Djangos `SECRET_KEY`. Måste sättas till ett genererat värde i produktion. |
| `SALDOVIBE_FIELD_ENCRYPTION_KEY` | härleds från `SECRET_KEY` | Fernet-nyckel för fältkryptering i vila (personnummer). Generera med `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Sätt den explicit i produktion — att rotera `DJANGO_SECRET_KEY` utan att den är satt gör krypterade fält oläsbara. |
| `SALDOVIBE_PUBLIC_URL` | (tom) | Kanonisk bas-URL som webbläsaren ser, t.ex. `https://bokforing.example.se`. Används för att härleda `ALLOWED_HOSTS` och `CSRF_TRUSTED_ORIGINS` när de explicita variablerna nedan inte är satta. |
| `DJANGO_ALLOWED_HOSTS` | härleds från `SALDOVIBE_PUBLIC_URL`, annars `127.0.0.1,localhost,[::1]` | Kommaseparerad explicit överstyrning av `ALLOWED_HOSTS`. Använd vid flera domäner. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | härleds från `SALDOVIBE_PUBLIC_URL` | Kommaseparerad explicit överstyrning av `CSRF_TRUSTED_ORIGINS`. Använd vid flera origins. |
| `DJANGO_USE_X_FORWARDED_HOST` | `1` (sant) | Om `X-Forwarded-Host` från en reverse proxy ska litas på. |
| `DJANGO_USE_SECURE_PROXY_SSL_HEADER` | `1` (sant) | Om `X-Forwarded-Proto` från en reverse proxy ska litas på för att upptäcka HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `0` (falskt) | Sätt till `1` när appen bara serveras över HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `0` (falskt) | Sätt till `1` när appen bara serveras över HTTPS. |

Booleska flaggor accepterar `1`, `true`, `True`, `yes` eller `on`; allt annat tolkas som falskt.

## Platser för körtidsdata

| Variabel | Standard | Syfte |
|---|---|---|
| `SALDOVIBE_DATA_DIR` | projektroten | Baskatalog för sqlite-databas, uppladdad media och härledda körtidsfiler. |
| `SALDOVIBE_STATIC_ROOT` | `<datakatalog>/staticfiles` | Katalog för insamlade statiska filer (målet för `collectstatic`). |

## Databas

| Variabel | Standard | Syfte |
|---|---|---|
| `DATABASE_ENGINE` | `django.db.backends.sqlite3` | Sätt till `django.db.backends.postgresql` för produktion. Alla andra värden aktiverar de fullständiga Postgres-liknande anslutningsinställningarna nedan. |
| `DATABASE_NAME` | `saldovibe` | Postgres-databasens namn. |
| `DATABASE_USER` | `saldovibe` | Postgres-användare. |
| `DATABASE_PASSWORD` | (tom) | Postgres-lösenord. |
| `DATABASE_HOST` | `localhost` | Postgres-host (`db` i Docker Compose-nätverket). |
| `DATABASE_PORT` | `5432` | Postgres-port. |

Tjänsten `db` i `docker-compose.yml` läser dessutom `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD` (standardvariablerna för `postgres`-imagens initiering) — håll dem i synk med
`DATABASE_*`-värdena ovan, eftersom båda kommer från samma `.env`-fil.

## Containerns entrypoint

| Variabel | Standard | Syfte |
|---|---|---|
| `SALDOVIBE_RUN_MIGRATIONS` | `1` | När `1` kör `scripts/docker-entrypoint.sh` `manage.py migrate --noinput` vid varje containerstart innan den lämnar över till CMD (gunicorn). Sätt till `0` för att hoppa över, t.ex. om migrationer körs separat. Se [upgrades-migrations.md](upgrades-migrations.md). |

## Skatteverkets API (uppslag av preliminärskatt för löner)

| Variabel | Standard | Syfte |
|---|---|---|
| `SKATTEVERKET_API_BASE_URL` | publik Entryscape-dataset-URL | Bas-URL för skattetabells-API:et som används när en lönekörning avslutas. |
| `SKATTEVERKET_API_TAX_PATH` | (tom) | Valfri sökväg som läggs till efter bas-URL:en. |
| `SKATTEVERKET_API_KEY` | (tom) | API-nyckel, om den konfigurerade endpointen kräver en. |
| `SKATTEVERKET_API_BEARER_TOKEN` | (tom) | Bearer-token, om den konfigurerade endpointen kräver en. |
| `SKATTEVERKET_API_TIMEOUT` | `12` (sekunder) | Timeout för skatteuppslaget. |
| `SKATTEVERKET_API_STRICT` | `0` (falskt) | När sant behandlas misslyckade uppslag som hårda fel överallt där de sker, inte bara vid avslut av lönekörning. |
| `SKATTEVERKET_CA_BUNDLE` | (tom) | Sökväg till ett eget CA-paket, för miljöer med egen TLS-inspektion. |
| `SKATTEVERKET_USE_CERTIFI` | `1` (sant) | Använd det medföljande `certifi`-CA-paketet om inget eget anges. |

Att avsluta en lönekörning ([användarhandboken, kapitel 6](../user-guide/06-loner.md)) anropar det
här API:et synkront och **stoppar hela löneavslutet** om anropet inte lyckas — det finns medvetet
ingen tyst reservberäkning av skatten.

## Systemmejl (notisdigest)

Utgående e-post för **systemnotiser** (den dagliga digesten som skickas av `skicka_notisdigest`, se
`bookkeeping/outgoing_mail.py`). Kundriktad e-post (fakturor, betalningspåminnelser) skickas via
respektive företags eget konto som konfigureras i företagsinställningarna, inte via de här
variablerna — och ett företag kan även åsidosätta notisavsändaren där ("Notiser till användare":
eget SMTP-konto, Microsoft 365-brevlåda eller företagets utgående konto), i vilket fall de här
variablerna inte används för det företaget. Med tom `EMAIL_HOST` används Djangos console-backend —
mejl skrivs till loggen i stället för att skickas (standard i dev, och ett säkert läge för
okonfigurerad produktion).

| Variabel | Standard | Syfte |
|---|---|---|
| `EMAIL_HOST` | (tom) | SMTP-server för systemmejl. Tom = console-backend, ingenting skickas. |
| `EMAIL_PORT` | `587` | SMTP-port. |
| `EMAIL_HOST_USER` | (tom) | SMTP-användarnamn. |
| `EMAIL_HOST_PASSWORD` | (tom) | SMTP-lösenord. |
| `EMAIL_USE_TLS` | `1` (sant) | STARTTLS på anslutningen. |
| `DEFAULT_FROM_EMAIL` | `saldovibe@localhost` | Avsändaradress på systemmejl. |

## ReInvGrabber (fältutläsning från bilagor)

Körs i samma process (se `attachments/extraction_client.py` och beroendet
`reinvgrabber-extraction` i `requirements.txt`, hämtat från
https://github.com/montegrotto/ReInvGrabber) — ingen separat tjänst eller URL att konfigurera.
Kräver Tesseract OCR-binären + svensk språkdata på `PATH`; redan installerat i Dockerfile.

| Variabel | Standard | Syfte |
|---|---|---|
| `REINVGRABBER_ENABLED` | `1` (sant) | Sätt till `0` för att stänga av utläsningen helt — uppladdning av bilagor fungerar då exakt som förut, ingen OCR körs. |

## Var de sätts i praktiken

Allt ligger i rotens `.env` (git-ignorerad) — kopiera `.env.example`. För lokal utveckling ger
standardvärdena sqlite + debug av (avkommentera `DJANGO_DEBUG=1`); för produktion avkommenterar
och fyller du i avsnittet "Produktion", se [deploy-checklist.md](deploy-checklist.md).
