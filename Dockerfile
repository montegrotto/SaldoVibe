FROM python:3.13-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
# Templates only ever reference one minified file per vendor package (plus the
# bootstrap-icons font files) — grepped across templates/, static/, and app code to
# confirm. STATICFILES_DIRS points at the whole npm dist/ folders though, so without
# this, whitenoise's CompressedManifestStaticFilesStorage hashes *and* gzips every
# unused source map, RTL variant, non-minified build, and locale file too.
RUN npm ci --omit=dev \
    && find node_modules/bootstrap/dist -type f ! -name 'bootstrap.min.css' ! -name 'bootstrap.bundle.min.js' -delete \
    && find node_modules/bootstrap-icons/font -type f ! -name 'bootstrap-icons.min.css' ! -name 'bootstrap-icons.woff' ! -name 'bootstrap-icons.woff2' -delete \
    && find node_modules/chart.js/dist -type f ! -name 'chart.umd.js' -delete \
    && find node_modules/jquery/dist -type f ! -name 'jquery.min.js' -delete \
    && find node_modules/select2/dist -type f ! -name 'select2.min.css' ! -name 'select2.min.js' -delete \
    && find node_modules/bootstrap/dist node_modules/bootstrap-icons/font node_modules/chart.js/dist node_modules/jquery/dist node_modules/select2/dist -depth -type d -empty -delete \
    # whitenoise's manifest storage errors out if a kept file's sourceMappingURL comment
    # points at a .map we just deleted, so drop the now-dangling comment instead.
    && sed -i '/sourceMappingURL/d' \
         node_modules/bootstrap/dist/css/bootstrap.min.css \
         node_modules/bootstrap/dist/js/bootstrap.bundle.min.js \
         node_modules/chart.js/dist/chart.umd.js

COPY requirements.lock ./
# --no-compile skips pip's forced bytecode precompilation (PYTHONDONTWRITEBYTECODE only
# affects bytecode written at import time, not pip's own install-time compileall pass),
# which otherwise leaves ~50MB of unused __pycache__ dirs baked into the venv layer.
RUN python -m venv /opt/venv \
    && . /opt/venv/bin/activate \
    && pip install --upgrade pip \
    && pip install --no-compile -r requirements.lock \
    && find /opt/venv -type d -name '__pycache__' -prune -exec rm -rf {} + \
    # GIS support needs GDAL/GEOS system libs we don't install and never use (no
    # django.contrib.gis in INSTALLED_APPS); the package code is dead weight.
    && rm -rf /opt/venv/lib/python*/site-packages/django/contrib/gis \
    # No LocaleMiddleware and a fixed LANGUAGE_CODE ('sv-se') mean only the sv (and
    # source-language en) translation catalogs are ever used; the other ~100 locales
    # Django ships are dead weight.
    && for d in $(find /opt/venv/lib/python*/site-packages/django -type d -name locale); do \
         find "$d" -mindepth 1 -maxdepth 1 -type d ! -name sv ! -name en -exec rm -rf {} + ; \
       done \
    # pip itself isn't needed at runtime; nothing in the app invokes it programmatically.
    && rm -rf /opt/venv/lib/python*/site-packages/pip /opt/venv/lib/python*/site-packages/pip-*.dist-info /opt/venv/bin/pip*

COPY . .

ENV PATH="/opt/venv/bin:$PATH" \
    SALDOVIBE_DATA_DIR=/data \
    SALDOVIBE_STATIC_ROOT=/app/staticfiles

RUN mkdir -p /data/media /app/staticfiles \
    && python manage.py collectstatic --noinput


FROM python:3.13-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SALDOVIBE_DATA_DIR=/data \
    SALDOVIBE_STATIC_ROOT=/app/staticfiles \
    DJANGO_SETTINGS_MODULE=saldovibe.settings

WORKDIR /app

# postgresql-client-17 is the Debian trixie default and matches the postgres:17-alpine server
# in docker-compose.prod.yml. Keep the two majors in step when bumping either: the client that
# pg_dump/pg_restore emit for is newer than the server it talks to as soon as they diverge, and
# the restore dry-run then fails with an unrecognized-configuration-parameter error (that is how
# a v17 client against the previous v16 server broke, on transaction_timeout).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates gosu postgresql-client-17 \
        tesseract-ocr tesseract-ocr-swe \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /app/manage.py /app/manage.py
COPY --from=builder --chown=appuser:appuser /app/saldovibe /app/saldovibe
COPY --from=builder --chown=appuser:appuser /app/accounts /app/accounts
COPY --from=builder --chown=appuser:appuser /app/attachments /app/attachments
COPY --from=builder --chown=appuser:appuser /app/auditlog /app/auditlog
COPY --from=builder --chown=appuser:appuser /app/banking /app/banking
COPY --from=builder --chown=appuser:appuser /app/bookkeeping /app/bookkeeping
COPY --from=builder --chown=appuser:appuser /app/expenses /app/expenses
COPY --from=builder --chown=appuser:appuser /app/fixed_assets /app/fixed_assets
COPY --from=builder --chown=appuser:appuser /app/invoicing /app/invoicing
COPY --from=builder --chown=appuser:appuser /app/payroll /app/payroll
COPY --from=builder --chown=appuser:appuser /app/supplier_invoices /app/supplier_invoices
COPY --from=builder --chown=appuser:appuser /app/vat /app/vat
COPY --from=builder --chown=appuser:appuser /app/templates /app/templates
COPY --from=builder --chown=appuser:appuser /app/static /app/static
COPY --from=builder --chown=appuser:appuser /app/staticfiles /opt/staticfiles
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY scripts/monthly_restore_dry_run.sh /usr/local/bin/monthly-restore-dry-run.sh

RUN chmod 755 /usr/local/bin/docker-entrypoint.sh /usr/local/bin/monthly-restore-dry-run.sh

# Ownership is set inline via COPY --chown above so overlayfs doesn't have to
# copy-up the entire venv/staticfiles/code tree a second time into this layer;
# only the freshly-created (empty) directories need it here.
RUN mkdir -p /data/media /app/staticfiles \
    && chown -R appuser:appuser /data /app/staticfiles

VOLUME ["/data"]

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "saldovibe.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-"]