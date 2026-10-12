#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/runtime/$RUN_ID"; PIDF="$RT/podwatch.pid"
touch "$RT/PODWATCH_STOP"
if [[ -f "$PIDF" ]]; then
  PID=$(cat "$PIDF")
  kill -TERM "$PID" 2>/dev/null || true
  for _ in $(seq 1 60); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  kill -0 "$PID" 2>/dev/null && { echo 'podwatch failed to stop' >&2; exit 3; }
  rm -f "$PIDF"
fi
echo 'podwatch stopped'
