#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RT="$ROOT/runtime/$RUN_ID"; TOK="$RT/tokens"; RES="$RT/results"; PIDF="$RT/podwatch.pid"
[[ -x "$ROOT/.venv_runner/bin/python" ]] || { echo 'runner venv missing' >&2; exit 2; }
if [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "already running $(cat "$PIDF")"; exit 0; fi
rm -f "$RT/PODWATCH_STOP"
setsid nohup "$ROOT/.venv_runner/bin/python" "$ROOT/shared/E39_LOCKED_BASE/src/pod_watch.py" \
  --api-server https://127.0.0.1:6443 --ca-file "$TOK/ca.crt" --token-file "$TOK/reset.token" \
  --namespace "e39-$RUN_ID" --out "$RES/pod_events.jsonl" --stop-file "$RT/PODWATCH_STOP" \
  > "$RES/podwatch_stdout.log" 2>&1 < /dev/null &
echo $! > "$PIDF"
sleep 3
kill -0 "$(cat "$PIDF")" 2>/dev/null || { cat "$RES/podwatch_stdout.log"; exit 3; }
echo "podwatch running pid $(cat "$PIDF")"
