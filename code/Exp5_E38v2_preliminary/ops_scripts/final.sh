#!/bin/bash
# Final: collect logs, final analysis (stage 2 = pooled, primary + sensitivities), independent recompute.
set -uo pipefail
RUN_ID=$1
E=~/e38v2_20260926
KC=$E/.kubeconfig_e38v2
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
tail -2 $R/runner.log
python3 $E/protocol/lock_hash.py verify $E/protocol/LOCK_E38v2.json | tee $R/LOCK_VERIFY_AT_EXPORT.txt
bash ~/e38v2_ops/collect_logs.sh $RUN_ID
k -n e38v2-system logs deploy/e38v2-webhook > $R/webhook_stdout.log 2>&1
cd $E/analysis
$PYA analyze_e38v2.py --run-dir $R --lock $E/protocol/LOCK_E38v2.json --stage 2 \
   --schedule $R/SCHEDULE.json --schedule $R/SCHEDULE_STAGE2.json --out $R/analysis_final
echo "final analysis rc=$?"
# stage-1-only rerun on the final files (S4 check that stage-2 records are ignored)
$PYA analyze_e38v2.py --run-dir $R --lock $E/protocol/LOCK_E38v2.json --stage 1 --out $R/analysis_stage1_on_final_files >/dev/null
echo "stage-1 re-analysis on final files rc=$?"
rm -rf $E/analysis/__pycache__
python3 ~/e38v2_ops/independent_recompute.py $R $R/analysis_final/cells_E38v2.csv 2 | tee $R/INDEPENDENT_RECOMPUTE.txt
