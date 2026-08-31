#!/usr/bin/env bash
# Scout bridge poll cycle, cron-friendly: sets up the environment cron doesn't have.
#
# Usage:
#   run_cycle.sh                # full cycle: ingest, reconcile, derive+publish (every 10 min)
#   run_cycle.sh designations   # MPC Previous-NEOCP outcome pass only (daily)
#
# Strict filters only — never add --relaxed-filters here; relaxed runs are manual.
set -uo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BRIDGE_DIR"

export DB_HOST=localhost DB_PORT=5433 DB_PASSWORD=scout_bridge
export SCOUT_TOPIC_URL=kafka://kafka.scimma.org/Scout.scout-test

PY="$BRIDGE_DIR/.venv/bin/python"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) cycle start (${1:-full}) ==="

if [ "${1:-}" = "designations" ]; then
    "$PY" manage.py updatescout --skip-reconcile
    exit $?
fi

# Idempotent; also brings the db back up after a reboot (compose has no restart policy).
docker compose up -d db --wait

"$PY" manage.py migrate --noinput
QUERY_ID="$("$PY" manage.py bootstrap_scout_query --print-id)" || exit 1
# NB: rundataquery catches its own failures and still exits 0 — watch log freshness, not exit codes.
"$PY" manage.py rundataquery "$QUERY_ID"
"$PY" manage.py updatescout --skip-designations
"$PY" manage.py publish_scout_events
