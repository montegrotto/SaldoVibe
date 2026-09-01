---
name: update-dependencies
description: Regenerate requirements.lock and requirements-dev.lock after editing requirements.txt or requirements-dev.txt. Use when adding, removing, or bumping a Python dependency.
---

**Dependencies are locked.** `requirements.txt` / `requirements-dev.txt` hold the direct
dependencies and are what you edit; `requirements.lock` / `requirements-dev.lock` are generated
from them with pinned transitive versions and sha256 hashes, and are what Docker, CI and your venv
actually install. Editing a requirements file without regenerating the lock changes nothing about
what ships — `scripts/check_requirements_lock.py` fails the build when they drift apart. After any
dependency change, regenerate both:

```bash
uv pip compile requirements.txt --generate-hashes --python-version 3.13 \
  --output-file requirements.lock
uv pip compile requirements-dev.txt --generate-hashes --python-version 3.13 \
  --output-file requirements-dev.lock
```

Use `uv`, not `pip-compile`: pip-tools 7.6 reads `pip._internal` internals that pip 26 removed and
crashes on import. The locks carry hashes for every platform wheel, so a lock generated on macOS
installs cleanly in the Linux image.
