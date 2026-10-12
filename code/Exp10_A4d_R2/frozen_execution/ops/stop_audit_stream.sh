#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; PIDF="$RT/audit_stream.pid"
if [[ -f "$PIDF" ]]; then
 PID=$(cat "$PIDF")
 if [[ -d "/proc/$PID" ]]; then sudo -n kill -TERM -- "-$PID" 2>/dev/null || sudo -n kill -TERM "$PID" 2>/dev/null || true; fi
 for _ in $(seq 1 30); do [[ ! -d "/proc/$PID" ]] && break; sleep 1; done
 [[ -d "/proc/$PID" ]] && { sudo -n kill -KILL -- "-$PID" 2>/dev/null || true; }
 rm -f "$PIDF"
fi
echo 'audit streamer stopped'
