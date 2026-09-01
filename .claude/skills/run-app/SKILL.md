---
name: run-app
description: Launch SaldoVibe and drive it in a real browser against a throwaway database. Use when asked to run, start, or screenshot the app, or to verify a UI/JS change works for real (CLAUDE.md's Verification Policy requires this for any UI/JS change — the Django test suite does not catch JS load-order bugs, disabled buttons, or broken previews).
---

# Running SaldoVibe

Django 5.2 LTS + server-rendered templates. No JS bundler, so "run it" means: start `runserver`
against a disposable database, drive headless Chromium at it, screenshot, read the screenshot.

**Never run against the dev `db.sqlite3`.** CLAUDE.md forbids writing throwaway data into it.
Everything below points `SALDOVIBE_DATA_DIR` at a scratch directory instead — that relocates the
sqlite file *and* media, so the dev database is untouched.

## 1. Environment

```bash
cd <repo root>
export SALDOVIBE_DATA_DIR="${TMPDIR:-/tmp}/saldovibe-run/appdata"   # disposable DB + media
export DJANGO_DEBUG=1
rm -rf "$SALDOVIBE_DATA_DIR" && mkdir -p "$SALDOVIBE_DATA_DIR"
```

`DJANGO_DEBUG=1` is what makes static work without a collected `STATIC_ROOT`: it turns off the
manifest lookup in `{% static %}` and puts WhiteNoise into finders mode, so assets are served
straight out of `static/` and `node_modules`. No `collectstatic` needed for this workflow — just
make sure `node_modules` exists (`npm ci`), since the vendor assets live there.

```bash
.venv/bin/python manage.py migrate --noinput
.venv/bin/python .claude/skills/run-app/seed_demo_data.py
```

The seeder prints the login and the bank-transaction id. It creates a company with the full BAS
chart, a 2026 accounting year, a customer + article, a bank account with one unbooked transaction,
and two PNG attachments — enough to open every form that matters.

## 2. Start / stop

```bash
.venv/bin/python manage.py runserver 8731 --noreload &     # --noreload: the reloader survives kills
until curl -s -o /dev/null http://127.0.0.1:8731/; do sleep 1; done

lsof -ti:8731 -sTCP:LISTEN | xargs kill                    # stop
```

Pick an unusual port — 8000 is often already taken by the user's own server, and killing that
would take down their session. Don't `pkill -f runserver`.

### Against Postgres instead of sqlite

The suite and everything above run sqlite; production runs Postgres via `psycopg`. After a Django
or `psycopg` bump, re-check on the real engine — constraint and migration behaviour is where the
two diverge, and sqlite is the forgiving one. `DATABASE_ENGINE` switches it; the rest of the
`DATABASE_*` vars only apply when it is not sqlite.

```bash
docker run -d --name saldovibe-pgtest \
  -e POSTGRES_DB=saldovibe -e POSTGRES_USER=saldovibe -e POSTGRES_PASSWORD=testpass \
  -p 55432:5432 postgres:17-alpine        # 17 matches docker-compose.prod.yml

# Wait on a real query, NOT pg_isready — see gotcha below.
until docker exec saldovibe-pgtest psql -U saldovibe -d saldovibe -c "select 1" >/dev/null 2>&1; do sleep 1; done

export DATABASE_ENGINE=django.db.backends.postgresql
export DATABASE_NAME=saldovibe DATABASE_USER=saldovibe DATABASE_PASSWORD=testpass
export DATABASE_HOST=127.0.0.1 DATABASE_PORT=55432

.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py makemigrations --check --dry-run   # schema drift shows up here first
.venv/bin/python manage.py test

docker rm -f saldovibe-pgtest            # stop
```

Port 55432 keeps it clear of a local 5432. The container is disposable — no volume, so
`docker rm -f` takes the data with it and the dev sqlite file is never involved.

## 3. Drive it

No `chromium-cli` here. Playwright is **not** a project dependency, so it disappears whenever
`.venv` is rebuilt. Install it into a throwaway venv rather than the project one:

```bash
python3.13 -m venv /tmp/pwvenv && /tmp/pwvenv/bin/pip install -q playwright
```

The browser binaries live in the shared `~/Library/Caches/ms-playwright/` cache and survive that.
A fresh `playwright` release usually wants a newer Chromium build than the cache holds; rather than
re-downloading a few hundred MB, point at the build that is already there (`ls` the cache for the
build number).

```python
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8731"
CHROME = (
    "/Users/<you>/Library/Caches/ms-playwright/chromium_headless_shell-<build>"
    "/chrome-headless-shell-mac-arm64/chrome-headless-shell"
)

with sync_playwright() as p:
    page = p.chromium.launch(executable_path=CHROME).new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto(f"{BASE}/konton/login/")
    page.fill('input[name="username"]', "smoke-run@example.test")  # username field, not email
    page.fill('input[name="password"]', "smoke-pass-123")
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_load_state("networkidle")

    page.goto(f"{BASE}/transaktioner/ny/")
    page.fill('input[name="description"]', "Smoke")
    page.screenshot(path="/tmp/shot.png", full_page=True)
    print(errors)  # always check — a page renders fine while its JS throws
```

**Look at the screenshot** with the Read tool. A blank or half-rendered frame is a failure.

Useful URLs (all need a logged-in session with an active company):

| Form | URL |
|---|---|
| Verifikation | `/transaktioner/ny/` |
| Kundfaktura | `/kundfakturor/ny/` |
| Leverantörsfaktura | `/leverantorsfakturor/ny/` |
| Bankbokföring | `/banking/transaktioner/<id>/bokfor/` |
| Bilageväljare | `/bilagor/valj/?return_to=…&selected=…` |

### Attachment-picker round-trip

The four forms above share `static/js/attachment-picker.js`. Verifying it means proving unsaved
input survives the trip to the picker page and back:

```python
page.fill('input[name="description"]', "Roundtrip")
page.click("#add-row-btn")  # grow a formset row first
page.fill('input[name="entries-0-debit"]', "1234.50")
page.click("#open-attachment-picker")
page.check('input[name="attachment_ids"] >> nth=0')
page.click('button[type="submit"]:has-text("Använd")')
# now assert: TOTAL_FORMS back, field values back, row in #selected-attachments-list,
# #no-selected-attachments gone, and sessionStorage cleaned up
```

Assert on the *formset* path, not just a plain text field — `growRows()` re-adding rows before
values are applied is the part that actually breaks.

## Gotchas

- **The static manifest only bites with `DEBUG=False`.** `STORAGES["staticfiles"]` is WhiteNoise's
  `CompressedManifestStaticFilesStorage`, so with `DEBUG` off `{% static %}` raises
  `ValueError: Missing staticfiles manifest entry` for anything not collected — and the error names
  the static file, not the template, which reads like a template bug. With `DJANGO_DEBUG=1` (this
  workflow) it never fires. Don't conclude from a green browser run that `collectstatic` is
  unnecessary: `manage.py test` runs `DEBUG=False` and *does* need it after any `static/` change.
- **Vendor static lives in `node_modules`,** wired in via `STATICFILES_DIRS`. Missing it gives an
  unstyled page in DEBUG mode, and a manifest with zero `vendor/` entries otherwise —
  `collectstatic` still exits 0, so the failure is silent until something renders. Run `npm ci`.
- **`npm ci --dry-run` deletes `node_modules` anyway.** `npm ci` wipes the directory before it
  looks at `--dry-run`, so the "safe" check is destructive. To validate the lockfile without that,
  read `package-lock.json` directly; to recover, just run `npm ci`.
- **`DJANGO_DEBUG=1` is required** for `runserver` to serve static/media (`SERVE_STATIC = DEBUG`).
- **Playwright locators are lazy.** `rows = page.locator(...)` then comparing against `rows.count()`
  *after* a mutation re-queries the DOM and silently compares the wrong numbers. Capture counts into
  plain ints before acting.
- **Select2 account fields** can't be driven with `fill` — they're jQuery widgets over a hidden
  select. Either set the underlying select and `$(el).trigger('change')` via `page.evaluate`, or
  avoid accounts and assert on plain inputs (debit/credit, description) instead.
- **PDF endpoints stream a download,** so `page.goto()` raises `Download is starting`. Fetch them
  with `page.request.get(url)` and assert the body starts with `b"%PDF-"` — worth doing after any
  dependency bump, since `xhtml2pdf` drives every report.
- **`pg_isready` lies during container startup.** The postgres image runs `initdb` against a
  temporary server first, so `pg_isready` reports ready while `POSTGRES_DB` still does not exist
  and the next command dies with `FATAL: database "saldovibe" does not exist`. Poll an actual
  query instead: `until docker exec … psql -U … -c "select 1"; do sleep 1; done`.
- **Don't pipe `manage.py test` through `tail`.** The summary (`Ran N tests` / `OK`) goes to
  stderr while app `print()`s go to block-buffered stdout, so with `2>&1 | tail -N` the buffered
  stdout flushes last and truncates the result away — leaving an exit code of 0 and no way to tell
  whether it passed. Redirect the whole thing to a file and grep it.
- **`timeout` isn't on macOS** by default. Use `until curl …; do sleep 1; done`, not `timeout 30 …`.
- **The login field is `username`,** even though the user model authenticates by email.
- **Verify you didn't touch the dev DB** when in doubt — query it for the seeder's org number:
  `sqlite3 db.sqlite3 "select count(*) from bookkeeping_company where org_number='556000-0042';"`
  should be `0`. Note there is unrelated older `smoke@example.com` test data in the dev database;
  don't mistake it for your own.
