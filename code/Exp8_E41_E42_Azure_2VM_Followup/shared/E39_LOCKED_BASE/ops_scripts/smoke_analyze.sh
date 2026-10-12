#!/bin/bash
# E39 smoke analysis (not confirmatory). Usage: smoke_analyze.sh RUN_ID SEED
set -uo pipefail
RUN_ID=$1; SEED=$2
E=~/e39_20260926
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
tail -3 $R/runner.log
bash ~/e39_ops/collect_logs.sh $RUN_ID $R/smoke_inputs
rm -f $R/LOCK_SMOKE.json
cd $E
python3 protocol/lock_hash_e39.py fill protocol/LOCK_TEMPLATE_E39.json $R/LOCK_SMOKE.json --root $E --seed $SEED \
   --run-id $RUN_ID --schedule results/$RUN_ID/SCHEDULE.json --analysis-plan protocol/ANALYSIS_PLAN_E39.txt \
   --e38v2r-lock ~/e38v2r_20260926/protocol/LOCK_E38v2.json --probe-tools; echo "fill rc=$?"
cd $E/analysis
$PYA oracle_e39.py --raw $R/RAW_TRIALS.jsonl --webhook $R/webhook_log.jsonl --audit $R/audit.log \
   --pod-events $R/smoke_inputs/pod_events.jsonl --out $R/oracle_smoke.csv; echo "oracle rc=$?"
$PYA analyze_e39.py --run-dir $R --lock $R/LOCK_SMOKE.json --lock-root $E --pod-events $R/smoke_inputs/pod_events.jsonl \
   --stage 1 --out $R/analysis_smoke --design-check off --bootstrap-reps 200 --w-external A2=40; echo "analysis rc=$?"
$PYA analyze_e39.py --run-dir $R --lock $R/LOCK_SMOKE.json --lock-root $E --pod-events $R/smoke_inputs/pod_events.jsonl \
   --stage 1 --out $R/analysis_smoke_strict --bootstrap-reps 200 2>&1 | tail -3
echo "strict rc=$? (refusal expected: 1-rep design)"
$PYA recompute_independent_e39.py --raw $R/RAW_TRIALS.jsonl --audit $R/audit.log --schedule $R/SCHEDULE.json \
   --compare $R/analysis_smoke/results_E39.json --out $R/analysis_smoke/independent_E39.json | tail -5; echo "recompute rc=$?"
$E/venv_runner/bin/python ~/e39_ops/quality_gates.py $E $R $R/oracle_smoke.csv $R/QUALITY_GATES_SMOKE.json
rm -rf $E/analysis/__pycache__
