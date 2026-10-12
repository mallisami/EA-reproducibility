#!/bin/bash
# Final: stop the pod-event recorder, collect logs, final analysis (stage 2 pooled; primary + sensitivities),
# stage-1-only re-analysis, independent recomputation, descriptive reports, first-run comparison.
set -uo pipefail
RUN_ID=$1
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
FIRST=~/e38v2_20260926/results/e38v2-20260926t0813z/analysis_final
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
tail -2 $R/runner.log
bash ~/e38v2r_ops/podwatch.sh stop $RUN_ID
python3 $E/protocol/lock_hash.py verify $E/protocol/LOCK_E38v2.json | tee $R/LOCK_VERIFY_AT_EXPORT.txt
bash ~/e38v2r_ops/collect_logs.sh $RUN_ID
k -n e38v2-system logs deploy/e38v2-webhook > $R/webhook_stdout.log 2>&1
cd $E/analysis
$PYA analyze_e38v2.py --run-dir $R --lock $E/protocol/LOCK_E38v2.json --stage 2 --pod-events $R/pod_events.jsonl \
   --schedule $R/SCHEDULE.json --schedule $R/SCHEDULE_STAGE2.json --out $R/analysis_final
echo "final analysis rc=$?"
$PYA analyze_e38v2.py --run-dir $R --lock $E/protocol/LOCK_E38v2.json --stage 1 --pod-events $R/pod_events.jsonl \
   --out $R/analysis_stage1_on_final_files >/dev/null
echo "stage-1 re-analysis on final files rc=$?"
rm -rf $E/analysis/__pycache__
cd ~/e38v2r_ops
python3 independent_recompute.py $R $R/analysis_final/cells_E38v2.csv 2 $R/pod_events.jsonl | tee $R/INDEPENDENT_RECOMPUTE.txt
python3 running_pod_report.py $R $R/analysis_final/oracle_trials_E38v2.csv $R/pod_events.jsonl $R/RUNNING_POD_REPORT.json > $R/RUNNING_POD_REPORT.txt
tail -3 $R/RUNNING_POD_REPORT.txt
python3 apiserver_webhook_map.py $R $R/apiserver.log $R/analysis_final/oracle_trials_E38v2.csv $R/MECHANISM_APISERVER.json > $R/MECHANISM_APISERVER_SUMMARY.txt
head -40 $R/MECHANISM_APISERVER_SUMMARY.txt
python3 a3_sensitivity.py $R > $R/A3_MULTI_EVAL_SENSITIVITY.txt
python3 compare_runs.py $FIRST $R/analysis_final $R/COMPARISON_FIRST_VS_RERUN.csv > $R/COMPARISON_FIRST_VS_RERUN.txt
tail -30 $R/COMPARISON_FIRST_VS_RERUN.txt
python3 headline.py $R > $R/HEADLINE_TABLES.txt
