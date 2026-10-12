#!/usr/bin/env bash
set -euo pipefail
MODE=${1:?usage: $0 smoke|main}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$MODE" in
 smoke) RUN_ID=e41s-20260930; SCHED="$ROOT/E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_SMOKE.json"; LOCK="$ROOT/E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_SMOKE.json";;
 main)  RUN_ID=e41r-20260930; SCHED="$ROOT/E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_MAIN.json";  LOCK="$ROOT/E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_MAIN.json";;
 *) echo 'mode must be smoke or main' >&2; exit 2;;
esac
python3 "$ROOT/tools/verify_freeze.py" --root "$ROOT"
bash "$ROOT/ops/setup_run.sh" "$RUN_ID"
bash "$ROOT/ops/start_podwatch.sh" "$RUN_ID"
RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"; TOK="$RT/tokens"
cleanup(){ bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [[ "$MODE" == smoke ]]; then
  "$ROOT/.venv_runner/bin/python" "$ROOT/E41_MULTIHOST_REPLICATION/preflight/preflight_e41.py" --run-id "$RUN_ID" --token-dir "$TOK" --out "$RES/PREFLIGHT_E41.json"
  bash "$ROOT/ops/snapshot_audit.sh" "$RES/PREFLIGHT_AUDIT.log"
  "$ROOT/.venv_runner/bin/python" "$ROOT/E41_MULTIHOST_REPLICATION/preflight/validate_preflight_audit.py" \
    --audit "$RES/PREFLIGHT_AUDIT.log" --probe "$RES/PREFLIGHT_E41.json" --out "$RES/PREFLIGHT_AUDIT_CHECK.json"
fi

set +e
E41_TOKEN_DIR="$TOK" E41_CA_FILE="$TOK/ca.crt" \
  "$ROOT/.venv_runner/bin/python" "$ROOT/E41_MULTIHOST_REPLICATION/scripts/e41_main.py" \
    --run --run-id "$RUN_ID" --schedule "$SCHED" --lock "$LOCK" --output-dir "$RES"
RC=$?
set -e
bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" || true
trap - EXIT
bash "$ROOT/ops/collect_run.sh" "$RUN_ID"
set +e
bash "$ROOT/ops/analyze_e41.sh" "$RUN_ID" "$SCHED" "$LOCK"
ARC=$?
if [[ "$MODE" == smoke && $ARC -eq 0 ]]; then
  "$ROOT/.venv_runner/bin/python" "$ROOT/E41_MULTIHOST_REPLICATION/analysis/validate_e41_smoke.py" --raw "$RES/RAW_TRIALS.jsonl" || ARC=$?
fi
set -e
if [[ $RC -ne 0 || $ARC -ne 0 ]]; then
  echo "E41 $MODE did not complete cleanly: runner=$RC analysis=$ARC" >&2; exit 5
fi
if [[ "$MODE" == smoke ]]; then
  echo 'E41 SMOKE complete. Inspect PREFLIGHT_* and E41_SUMMARY.json. Smoke data MUST NOT be pooled with main.'
else
  echo 'E41 MAIN complete. Preserve runtime/e41r-20260930 before any teardown.'
fi
