#!/usr/bin/env python3
"""Verify the lockfiles still agree with the requirements files they were built from.

Docker and CI install from requirements.lock, not requirements.txt. Bumping a
version in requirements.txt without regenerating the lock therefore changes
nothing about what actually gets deployed — the edit looks applied, `pip install`
succeeds, and the old version ships. This catches that.

Deliberately offline and does not re-resolve: regenerating and diffing would fail
whenever an unrelated transitive dependency publishes a new release, which makes
for a flaky gate. Checking that every *direct* requirement appears in the lock at
the same version catches the mistake people actually make.

Stdlib-only so it can run without the venv (CI lint job, pre-commit hook).
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAIRS = [
    ("requirements.txt", "requirements.lock"),
    ("requirements-dev.txt", "requirements-dev.lock"),
]
# `psycopg[binary]==3.2.10` locks as `psycopg==3.2.10` plus a separate
# `psycopg-binary` entry; the extra is not part of the locked project name.
REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*(?P<version>[^\s;#]+)")


def canonical(name):
    """PEP 503 normalisation — 'pyHanko' and 'pyhanko' are the same project."""
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_requirements(path):
    pins = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = REQUIREMENT.match(line)
        if match:
            pins[canonical(match.group("name"))] = match.group("version")
    return pins


def locked_versions(path):
    pins = {}
    for raw in path.read_text().splitlines():
        if raw.startswith((" ", "\t", "#")):
            continue
        match = REQUIREMENT.match(raw.strip())
        if match:
            pins[canonical(match.group("name"))] = match.group("version")
    return pins


def main():
    problems = []
    for source_name, lock_name in PAIRS:
        source = REPO_ROOT / source_name
        lock = REPO_ROOT / lock_name
        if not lock.is_file():
            problems.append(f"{lock_name} is missing; regenerate it (see CLAUDE.md)")
            continue

        direct = direct_requirements(source)
        locked = locked_versions(lock)
        if not direct:
            problems.append(f"parsed no pinned requirements out of {source_name}")
            continue

        for name, version in sorted(direct.items()):
            if name not in locked:
                problems.append(f"{source_name} pins '{name}' but {lock_name} does not contain it")
            elif locked[name] != version:
                problems.append(f"{source_name} pins {name}=={version} but {lock_name} has {name}=={locked[name]}")

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "\nRegenerate with:\n"
            "  uv pip compile requirements.txt --generate-hashes --python-version 3.13 "
            "--output-file requirements.lock\n"
            "  uv pip compile requirements-dev.txt --generate-hashes --python-version 3.13 "
            "--output-file requirements-dev.lock",
            file=sys.stderr,
        )
        sys.exit(1)

    total = sum(len(direct_requirements(REPO_ROOT / source)) for source, _ in PAIRS)
    print(f"ok: all {total} direct requirements match their lockfiles")


if __name__ == "__main__":
    main()
