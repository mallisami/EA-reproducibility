#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}; SCHED=${2:?}; LOCK=${3:?}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RES="$ROOT/runtime/$RUN_ID/results"; OUT="$RES/analysis_a4d"; PY="$ROOT/.venv_analysis/bin/python"
[[ -x "$PY" ]] || { echo 'analysis venv missing' >&2; exit 2; }
"$PY" "$ROOT/shared/E39_LOCKED_BASE/analysis/analyze_e39.py" --run-dir "$RES" --raw "$RES/RAW_TRIALS.jsonl" --webhook "$RES/webhook_log.jsonl" --audit "$RES/audit.log" --pod-events "$RES/pod_events.jsonl" --schedule "$SCHED" --lock "$LOCK" --lock-root "$ROOT" --analysis-plan "$ROOT/shared/E39_LOCKED_BASE/protocol/ANALYSIS_PLAN_E39.txt" --stage 1 --experiments A --design-check off --out "$OUT" --bootstrap-reps 2000
"$PY" "$ROOT/A4D_CONFIRMATION/analysis/summarize_a4d.py" --analysis-dir "$OUT" --out "$RES/A4D_CONFIRMATION_SUMMARY.json"
