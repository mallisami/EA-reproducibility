#!/usr/bin/env bash
set -euo pipefail
MODE=${1:?usage: $0 smoke|main}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$MODE" in
 smoke) RUN_ID=e42s-20260930; SCHED="$ROOT/E42_BENIGN_CHURN/protocol/SCHEDULE_E42_SMOKE.json"; LOCK="$ROOT/E42_BENIGN_CHURN/protocol/LOCK_E42_SMOKE.json";;
 main)  RUN_ID=e42b-20260930; SCHED="$ROOT/E42_BENIGN_CHURN/protocol/SCHEDULE_E42_MAIN.json";  LOCK="$ROOT/E42_BENIGN_CHURN/protocol/LOCK_E42_MAIN.json";;
 *) echo 'mode must be smoke or main' >&2; exit 2;;
esac
python3 "$ROOT/tools/verify_freeze.py" --root "$ROOT"
bash "$ROOT/ops/setup_run.sh" "$RUN_ID"
bash "$ROOT/ops/start_podwatch.sh" "$RUN_ID"
RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"; TOK="$RT/tokens"
cleanup(){ bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
set +e
E41_TOKEN_DIR="$TOK" E41_CA_FILE="$TOK/ca.crt" \
  "$ROOT/.venv_runner/bin/python" "$ROOT/E42_BENIGN_CHURN/scripts/e42_benign_churn.py" \
    --run --run-id "$RUN_ID" --schedule "$SCHED" --lock "$LOCK" --output-dir "$RES"
RC=$?
set -e
bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" || true
trap - EXIT
bash "$ROOT/ops/collect_run.sh" "$RUN_ID"
set +e
"$ROOT/.venv_runner/bin/python" "$ROOT/E42_BENIGN_CHURN/scripts/analyze_e42.py" --raw "$RES/RAW_TRIALS_E42.jsonl" --out "$RES/analysis_e42"
ARC=$?
if [[ "$MODE" == smoke && $ARC -eq 0 ]]; then
  "$ROOT/.venv_runner/bin/python" "$ROOT/E42_BENIGN_CHURN/analysis/validate_e42_smoke.py" --raw "$RES/RAW_TRIALS_E42.jsonl" || ARC=$?
fi
set -e
if [[ $RC -ne 0 || $ARC -ne 0 ]]; then echo "E42 $MODE failed: runner=$RC analysis=$ARC" >&2; exit 5; fi
if [[ "$MODE" == smoke ]]; then
  echo 'E42 SMOKE complete. Confirm benign_seq_value changes and all_target_validity_annotations_true before main. Do not pool smoke.'
else
  echo 'E42 MAIN complete. Preserve runtime/e42b-20260930 before teardown.'
fi
