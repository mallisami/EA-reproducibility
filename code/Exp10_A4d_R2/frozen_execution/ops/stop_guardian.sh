#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; PIDF="$RT/guardian.pid"; RES="$RT/results"
# Guardian watches RES/STOP, the same stop file used by the runner.
touch "$RES/STOP"
if [[ -f "$PIDF" ]]; then PID=$(cat "$PIDF"); kill -TERM "$PID" 2>/dev/null || true; for _ in $(seq 1 20); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done; kill -KILL "$PID" 2>/dev/null || true; rm -f "$PIDF"; fi
echo 'audit guardian stopped'
