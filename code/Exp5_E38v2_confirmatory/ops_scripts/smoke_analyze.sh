#!/bin/bash
# E38v2 RERUN smoke analysis. Usage: smoke_analyze.sh RUN_ID SEED
set -uo pipefail
RUN_ID=$1; SEED=$2
E=~/e38v2r_20260926
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
tail -3 $R/runner.log
bash ~/e38v2r_ops/collect_logs.sh $RUN_ID $R/smoke_inputs
rm -f $R/LOCK_SMOKE.json
python3 $E/protocol/lock_hash.py fill $E/protocol/LOCK_TEMPLATE_E38v2.json $R/LOCK_SMOKE.json --root $E --seed $SEED \
   --run-id $RUN_ID --schedule results/$RUN_ID/SCHEDULE.json --analysis-plan protocol/ANALYSIS_PLAN_E38v2.txt
cd $E/analysis
$PYA oracle.py --raw $R/RAW_TRIALS.jsonl --webhook $R/webhook_log.jsonl --audit $R/audit.log \
   --pod-events $R/smoke_inputs/pod_events.jsonl --out $R/oracle_smoke.csv
$PYA analyze_e38v2.py --run-dir $R --lock $R/LOCK_SMOKE.json --lock-root $E --analysis-plan $E/protocol/ANALYSIS_PLAN_E38v2.txt \
   --pod-events $R/smoke_inputs/pod_events.jsonl --stage 1 --out $R/analysis_smoke --design-check off --bootstrap-reps 200; echo "analysis rc=$?"
$PYA analyze_e38v2.py --run-dir $R --lock $R/LOCK_SMOKE.json --lock-root $E --analysis-plan $E/protocol/ANALYSIS_PLAN_E38v2.txt \
   --pod-events $R/smoke_inputs/pod_events.jsonl --stage 1 --out $R/analysis_smoke_strict --bootstrap-reps 200 2>&1 | head -3
echo "strict rc=$? (refusal expected: 1-rep design)"
python3 ~/e38v2r_ops/smoke_stats.py $R $R/oracle_smoke.csv
python3 ~/e38v2r_ops/apiserver_webhook_map.py $R $R/apiserver.log $R/oracle_smoke.csv $R/MECHANISM_SMOKE.json | tail -40
rm -rf $E/analysis/__pycache__
