#!/usr/bin/env bash
set -euo pipefail

# docker exec defaults to root now that the entrypoint (not the image's USER)
# is responsible for dropping to appuser. Re-exec as appuser so evidence files
# under /data stay owned by the same user the app runs as.
if [[ "$(id -u)" -eq 0 ]] && command -v gosu >/dev/null 2>&1; then
  exec gosu appuser "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/../manage.py" ]]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [[ -f "/app/manage.py" ]]; then
  REPO_ROOT="/app"
else
  echo "Could not locate manage.py (checked $SCRIPT_DIR/.. and /app)" >&2
  exit 1
fi
cd "$REPO_ROOT"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  : # explicit override wins
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "/opt/venv/bin/python" ]]; then
  PYTHON_BIN="/opt/venv/bin/python"
else
  PYTHON_BIN="python"
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

OUTPUT_DIR="${COMPLIANCE_EVIDENCE_DIR:-docs/compliance/evidence/restore-tests}"

"$PYTHON_BIN" manage.py compliance_restore_dry_run --output-dir "$OUTPUT_DIR"
"$PYTHON_BIN" manage.py verify_audit_chain
"$PYTHON_BIN" manage.py verify_audit_chain_anchors
"$PYTHON_BIN" manage.py reconcile_audit_log

# The external TSA is a network dependency on top of what was previously a fully
# offline check - a transient outage there shouldn't fail the whole monthly job.
# A missed anchor just means next month's window of unattested history is longer;
# it does not weaken any anchor already recorded.
if ! "$PYTHON_BIN" manage.py anchor_audit_chain; then
  echo "WARNING: could not reach the timestamp authority to anchor the audit chain this month." >&2
fi

echo "Monthly restore dry-run and audit-chain verification completed."
