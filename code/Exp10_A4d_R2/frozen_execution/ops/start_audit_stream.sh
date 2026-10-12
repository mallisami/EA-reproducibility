#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"; PIDF="$RT/audit_stream.pid"; SRC=/var/lib/rancher/k3s/server/logs/audit.log; OUT="$RES/audit_stream.jsonl"; LOG="$RES/audit_stream_stderr.log"
[[ -f "$SRC" ]] || { echo "audit source missing: $SRC" >&2; exit 3; }
[[ ! -e "$OUT" ]] || { echo "refusing to overwrite existing $OUT" >&2; exit 3; }
mkdir -p "$RES"
# tail -F is deliberately independent of kube-apiserver's backup-retention window.
setsid sudo -n tail -n 0 -F "$SRC" >> "$OUT" 2>> "$LOG" < /dev/null &
echo $! > "$PIDF"
sleep 2
PID=$(cat "$PIDF")
[[ -d "/proc/$PID" ]] || { echo 'audit streamer failed to start' >&2; cat "$LOG" >&2 || true; exit 4; }
echo "audit streamer running pid=$PID -> $OUT"
