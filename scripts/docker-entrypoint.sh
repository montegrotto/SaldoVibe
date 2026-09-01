#!/bin/sh
set -eu

# Runs as root so it can fix ownership on the /data volume, which may have been
# populated by an older image build (or created externally) with different
# ownership than the current appuser uid/gid. Named volumes are never
# re-synced to the image's ownership on rebuild, so this has to self-heal
# here rather than relying on the chown baked into the image at build time.
mkdir -p /data/media /app/staticfiles
chown -R appuser:appuser /data /app/staticfiles

if [ -d /opt/staticfiles ]; then
    find /app/staticfiles -mindepth 1 -delete
    cp -a /opt/staticfiles/. /app/staticfiles/
    chown -R appuser:appuser /app/staticfiles
fi

if [ "${SALDOVIBE_RUN_MIGRATIONS:-1}" = "1" ]; then
    gosu appuser /opt/venv/bin/python /app/manage.py migrate --noinput
fi

exec gosu appuser "$@"