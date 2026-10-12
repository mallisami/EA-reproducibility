#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"; PIDF="$RT/guardian.pid"
[[ -f "$RT/audit_stream.pid" ]] || { echo 'audit stream must be started before guardian' >&2; exit 2; }
rm -f "$RES/STOP" "$RT/GUARDIAN_STOP"
setsid nohup "$ROOT/.venv_runner/bin/python" "$ROOT/ops/audit_guardian.py" --run-id "$RUN_ID" --raw "$RES/RAW_TRIALS.jsonl" --audit-stream "$RES/audit_stream.jsonl" --stream-pid-file "$RT/audit_stream.pid" --pod-events "$RES/pod_events.jsonl" --podwatch-pid-file "$RT/podwatch.pid" --stop-file "$RES/STOP" --status "$RES/AUDIT_GUARDIAN_STATUS.json" > "$RES/audit_guardian_stdout.log" 2>&1 < /dev/null &
echo $! > "$PIDF"; sleep 2
kill -0 "$(cat "$PIDF")" 2>/dev/null || { cat "$RES/audit_guardian_stdout.log" >&2; exit 3; }
echo "audit guardian running pid=$(cat "$PIDF")"
