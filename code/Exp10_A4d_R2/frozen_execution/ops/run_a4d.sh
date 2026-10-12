#!/usr/bin/env bash
set -euo pipefail
MODE=${1:?usage: $0 smoke|main}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "$MODE" in
 smoke) RUN_ID=a4dcs-r2-20261001; SCHED="$ROOT/A4D_CONFIRMATION/protocol/SCHEDULE_A4D_SMOKE.json"; LOCK="$ROOT/A4D_CONFIRMATION/protocol/LOCK_A4D_SMOKE.json"; EXPECT=26;;
 main) RUN_ID=a4dcr-r2-20261001; SCHED="$ROOT/A4D_CONFIRMATION/protocol/SCHEDULE_A4D_MAIN.json"; LOCK="$ROOT/A4D_CONFIRMATION/protocol/LOCK_A4D_MAIN.json"; EXPECT=985;;
 *) echo 'mode must be smoke or main' >&2; exit 2;;
esac
python3 "$ROOT/tools/verify_freeze.py" --root "$ROOT"
if [[ "$MODE" == main && ! -f "$ROOT/runtime/SMOKE_PASS_A4D" ]]; then echo "Refusing main: run and pass bash ops/run_a4d.sh smoke first." >&2; exit 3; fi
bash "$ROOT/ops/setup_run.sh" "$RUN_ID"
bash "$ROOT/ops/start_audit_stream.sh" "$RUN_ID"
bash "$ROOT/ops/start_podwatch.sh" "$RUN_ID"
RT="$ROOT/runtime/$RUN_ID"; RES="$RT/results"; TOK="$RT/tokens"
cleanup(){ bash "$ROOT/ops/stop_guardian.sh" "$RUN_ID" >/dev/null 2>&1 || true; bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" >/dev/null 2>&1 || true; bash "$ROOT/ops/stop_audit_stream.sh" "$RUN_ID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# Mandatory live evidence-path probe on BOTH smoke and main. It is excluded from scientific data.
A4D_TOKEN_DIR="$TOK" A4D_CA_FILE="$TOK/ca.crt" "$ROOT/.venv_runner/bin/python" "$ROOT/A4D_CONFIRMATION/preflight/preflight_a4d.py" --run-id "$RUN_ID" --token-dir "$TOK" --out "$RES/PREFLIGHT_A4D.json"
"$ROOT/.venv_runner/bin/python" "$ROOT/A4D_CONFIRMATION/preflight/validate_preflight_audit.py" --audit "$RES/audit_stream.jsonl" --pod-events "$RES/pod_events.jsonl" --probe "$RES/PREFLIGHT_A4D.json" --out "$RES/PREFLIGHT_AUDIT_CHECK.json"
# Online guardian starts only after preflight passed.
bash "$ROOT/ops/start_guardian.sh" "$RUN_ID"
set +e
A4D_TOKEN_DIR="$TOK" A4D_CA_FILE="$TOK/ca.crt" "$ROOT/.venv_runner/bin/python" "$ROOT/A4D_CONFIRMATION/scripts/a4d_confirmation.py" --run --run-id "$RUN_ID" --schedule "$SCHED" --lock "$LOCK" --output-dir "$RES"
RC=$?
set -e
# Give kube-apiserver/tail enough time to flush final ResponseComplete records before stopping the stream.
sleep 8
bash "$ROOT/ops/stop_guardian.sh" "$RUN_ID" || true
bash "$ROOT/ops/stop_podwatch.sh" "$RUN_ID" || true
bash "$ROOT/ops/stop_audit_stream.sh" "$RUN_ID" || true
trap - EXIT
bash "$ROOT/ops/collect_run.sh" "$RUN_ID"
# Instrumentation completeness is checked BEFORE scientific analysis.
set +e
"$ROOT/.venv_runner/bin/python" "$ROOT/A4D_CONFIRMATION/analysis/validate_audit_evidence.py" --raw "$RES/RAW_TRIALS.jsonl" --audit "$RES/audit.log" --pod-events "$RES/pod_events.jsonl" --expected "$EXPECT" --out "$RES/AUDIT_EVIDENCE_COMPLETENESS.json"
EVC=$?
set -e
if [[ $RC -ne 0 || $EVC -ne 0 ]]; then
  echo "A4d $MODE is NOT a complete confirmation run: runner=$RC evidence_gate=$EVC" >&2
  echo 'Preserve the runtime directory and send it back. Do not interpret/merge as complete confirmation evidence.' >&2
  exit 6
fi
bash "$ROOT/ops/analyze_a4d.sh" "$RUN_ID" "$SCHED" "$LOCK"
if [[ "$MODE" == smoke ]]; then
  "$ROOT/.venv_runner/bin/python" "$ROOT/A4D_CONFIRMATION/analysis/validate_smoke.py" --raw "$RES/RAW_TRIALS.jsonl" --expected 26
  touch "$ROOT/runtime/SMOKE_PASS_A4D"
  echo 'A4D SMOKE PASS: 26/26 scheduled trials plus 100% required primary audit evidence. Smoke is excluded from the main analysis.'
else
  [[ -f "$ROOT/runtime/SMOKE_PASS_A4D" ]] || { echo 'Main completed, but this package has no recorded smoke PASS marker. Preserve data and contact reviewer before integration.' >&2; exit 7; }
  echo 'A4D MAIN COMPLETE: 985/985 scheduled confirmation trials and the primary audit-evidence gate passed.'
fi
