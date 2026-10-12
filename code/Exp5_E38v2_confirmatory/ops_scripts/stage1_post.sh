#!/bin/bash
# After stage 1: collect logs (pod-event snapshot), gate analysis (strict), top-up list, stage-2 schedule, LOCK add-stage2.
set -euo pipefail
RUN_ID=$1; SEED=$2
E=~/e38v2r_20260926
R=$E/results/$RUN_ID
PYA=$E/venv_analysis/bin/python
tail -2 $R/runner.log
python3 $E/protocol/lock_hash.py verify $E/protocol/LOCK_E38v2.json | tail -1
mkdir -p $R/stage1_inputs
bash ~/e38v2r_ops/collect_logs.sh $RUN_ID $R/stage1_inputs
cp $R/audit.log $R/stage1_inputs/audit_at_stage1.log
cp $R/webhook_log.jsonl $R/stage1_inputs/webhook_log_at_stage1.jsonl
cp $R/RAW_TRIALS.jsonl $R/stage1_inputs/RAW_TRIALS_at_stage1.jsonl
(cd $R/stage1_inputs && sha256sum audit_at_stage1.log webhook_log_at_stage1.jsonl RAW_TRIALS_at_stage1.jsonl pod_events.jsonl | tee SHA256SUMS)
cd $E/analysis
$PYA analyze_e38v2.py --run-dir $R --lock $E/protocol/LOCK_E38v2.json --pod-events $R/stage1_inputs/pod_events.jsonl \
   --stage 1 --out $R/analysis_stage1
echo "stage-1 analysis rc=$?"
$PYA stage2_topup.py --results $R/analysis_stage1/results_E38v2.json --out $R/stage2_topup
python3 -c "import json;d=json.load(open('$R/stage2_topup/stage2_cells_E38v2.json'));print('topup cells',d['n_cells'],'new trials',d['n_new_trials'],'undefined',len(d['undefined_cells']))"
cd $E/src
$E/venv_runner/bin/python e38v2_runner.py --make-schedule $R/SCHEDULE_STAGE2.json --stage 2 \
   --topup-file $R/stage2_topup/stage2_cells_E38v2.json --seed $SEED --run-id $RUN_ID | python3 -c "import json,sys;d=json.load(sys.stdin);print(json.dumps({k:d[k] for k in ('counts','runtime_estimate')}))"
cd $E
python3 protocol/lock_hash.py add-stage2 protocol/LOCK_E38v2.json --schedule2 results/$RUN_ID/SCHEDULE_STAGE2.json \
   --topup results/$RUN_ID/stage2_topup/stage2_cells_E38v2.json
python3 protocol/lock_hash.py verify protocol/LOCK_E38v2.json | tee $R/LOCK_VERIFY_STAGE2.txt
sha256sum protocol/LOCK_E38v2.json results/$RUN_ID/SCHEDULE_STAGE2.json | tee $R/LOCK_HASHES_STAGE2.txt
rm -rf $E/analysis/__pycache__
