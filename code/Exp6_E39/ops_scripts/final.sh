#!/bin/bash
# E39 final: stop recorder, verify LOCK, collect logs, final analysis (stage 2), independent recomputation,
# quality gates, headline. Usage: final.sh RUN_ID W_A2_EXTERNAL_MS
set -uo pipefail
RUN_ID=$1; WEXT=$2
E=~/e39_20260926
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
cd $E
date -u +%FT%TZ
bash ~/e39_ops/podwatch.sh stop $RUN_ID
python3 protocol/lock_hash_e39.py verify protocol/LOCK_E39.json | tee $R/LOCK_VERIFY_AT_EXPORT.txt | tail -2
bash ~/e39_ops/collect_logs.sh $RUN_ID
cp protocol/LOCK_E39.json $R/LOCK_E39.json
rm -rf $R/analysis_final
$PYA analysis/analyze_e39.py --run-dir $R --lock protocol/LOCK_E39.json --stage 2 \
   --schedule $R/SCHEDULE.json --schedule $R/SCHEDULE_STAGE2.json --pod-events $R/pod_events.jsonl \
   --w-external A2=$WEXT --out $R/analysis_final; echo "final analysis rc=$?"
$PYA analysis/recompute_independent_e39.py --raw $R/RAW_TRIALS.jsonl --audit $R/audit.log \
   --schedule $R/SCHEDULE.json --schedule $R/SCHEDULE_STAGE2.json \
   --compare $R/analysis_final/results_E39.json --out $R/analysis_final/independent_E39.json > $R/analysis_final/independent_stdout.txt 2>&1
echo "independent rc=$?"; tail -3 $R/analysis_final/independent_stdout.txt
$E/venv_runner/bin/python ~/e39_ops/quality_gates.py $E $R $R/analysis_final/oracle_trials_E39.csv $R/analysis_final/QUALITY_GATES_E39.json > /dev/null
python3 ~/e39_ops/headline.py $R/analysis_final/results_E39.json $R/analysis_final/independent_E39.json \
   $R/analysis_final/QUALITY_GATES_E39.json $R/HEADLINE_TABLES_E39.txt > /dev/null; echo "headline rc=$?"
python3 ~/e39_ops/d_split.py $R/RAW_TRIALS.jsonl $R/analysis_final/oracle_trials_E39.csv $R/EXPLORATORY_D_SPLIT_BY_CLUSTER_STOP.txt > /dev/null; echo "d_split rc=$?"
cp $E/results/OTHER_CLUSTERS_STOPPED.txt $R/OTHER_CLUSTERS_STOPPED.txt
rm -rf $E/analysis/__pycache__
date -u +%FT%TZ
