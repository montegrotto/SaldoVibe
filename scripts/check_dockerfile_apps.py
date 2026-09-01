#!/usr/bin/env python3
"""Verify every local Django app in INSTALLED_APPS is copied into the runtime image.

The runtime stage of the Dockerfile copies each app directory with an explicit
COPY line, so a newly added app that is missing there imports fine locally and
in the builder stage (COPY . .) but crashes the production container with
ModuleNotFoundError. Stdlib-only so it can run without the venv (CI lint job,
pre-commit hook).
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = REPO_ROOT / "saldovibe" / "settings.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def installed_apps():
    tree = ast.parse(SETTINGS.read_text(), filename=str(SETTINGS))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "INSTALLED_APPS" for t in node.targets
        ):
            return [
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    sys.exit(f"error: could not find INSTALLED_APPS in {SETTINGS}")


def main():
    local_apps = [app for app in installed_apps() if (REPO_ROOT / app / "__init__.py").is_file()]
    if not local_apps:
        sys.exit(f"error: found no local apps from INSTALLED_APPS in {REPO_ROOT}")

    copied = set(
        re.findall(
            r"^COPY\s+--from=builder(?:\s+--\S+)*\s+\S+\s+/app/(\S+)\s*$",
            DOCKERFILE.read_text(),
            flags=re.MULTILINE,
        )
    )
    missing = [app for app in local_apps if app not in copied]
    if missing:
        for app in missing:
            print(
                f"error: app '{app}' is in INSTALLED_APPS but the Dockerfile runtime "
                f"stage has no 'COPY --from=builder ... /app/{app}' line; the "
                "production image will fail with ModuleNotFoundError",
                file=sys.stderr,
            )
        sys.exit(1)
    print(f"ok: all {len(local_apps)} local apps are copied into the runtime image")


if __name__ == "__main__":
    main()
