---
paths:
  - "static/**"
  - "templates/**"
  - "package.json"
  - "saldovibe/settings.py"
---

### Frontend & static files

Server-rendered Django templates + Bootstrap 5, Chart.js, Select2, jQuery — vendored via
`node_modules` through `STATICFILES_DIRS`, no bundler. Page JS is inline in its template; shared
behaviour goes in `static/js/` under `window.SaldoVibe`, loaded in `{% block extra_js %}` after
vendor scripts.

**Static manifest gotcha:** WhiteNoise's manifest storage is active only with `DEBUG=False` —
`{% static 'x' %}` then raises `ValueError: Missing staticfiles manifest entry` (the error names
the file, not the template). With `DEBUG=True` the manifest is bypassed entirely. Consequences:

- `manage.py test` runs `DEBUG=False` → after adding/renaming anything under `static/`, run
  `collectstatic` or every template-rendering test fails.
- `STATIC_ROOT` defaults to `$DATA_DIR/staticfiles` (gitignored) — fresh checkouts have no manifest.
- Without `node_modules`, `collectstatic` exits 0 but silently writes zero `vendor/` entries —
  `npm ci` must come first. CI and the `Dockerfile` do `npm ci` + `collectstatic` before the
  suite.

### Template rendering rules

- `USE_THOUSAND_SEPARATOR` is deliberately `False` (see comment in `saldovibe/settings.py`) — it
  corrupts ids in `<option value>`, `data-*`, and URLs with a non-breaking space at 4+ digits.
  Don't re-enable; use `|floatformat:'Ng'` explicitly for money displays that need grouping.
- Guard nullable fields before concatenation (voucher series + number) so templates never render a
  literal `None` (`ANone`).
