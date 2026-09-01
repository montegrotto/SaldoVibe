#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    """Run administrative tasks."""
    # Populates os.environ from a root .env for bare `manage.py` runs (e.g. local
    # dev outside Docker). Never overrides variables already set in the environment,
    # so it's a no-op under Docker Compose, which sets vars via env_file/environment.
    load_dotenv(Path(__file__).resolve().parent / ".env")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "saldovibe.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
