#!/bin/bash
set -euo pipefail
RUN_ID=$1; SEED=$2
E=~/e38v2_20260926
R=$E/results/$RUN_ID
cd $E
$E/venv_runner/bin/python ~/e38v2_ops/probe_order.py $E $RUN_ID $R/PROBE_ORDER_OPS.json
sleep 2
sudo cat $E/results/_audit_host/audit.log | python3 ~/e38v2_ops/probe_order_audit.py $R/PROBE_ORDER_OPS.json $E | tee $R/PROBE_ORDER_RESULT.txt
cd $E/src
$E/venv_runner/bin/python e38v2_runner.py --make-schedule $R/SCHEDULE.json --stage 1 --seed $SEED --run-id $RUN_ID > $R/SCHEDULE_DRYRUN.json
python3 -c "import json;d=json.load(open('$R/SCHEDULE_DRYRUN.json'));print(json.dumps({k:d[k] for k in ('seed','run_id','counts','runtime_estimate')}))"
cd $E
test ! -e protocol/LOCK_E38v2.json
python3 protocol/lock_hash.py fill protocol/LOCK_TEMPLATE_E38v2.json protocol/LOCK_E38v2.json --seed $SEED --run-id $RUN_ID \
   --schedule results/$RUN_ID/SCHEDULE.json --analysis-plan protocol/ANALYSIS_PLAN_E38v2.txt --probe-tools
python3 protocol/lock_hash.py verify protocol/LOCK_E38v2.json | tee $R/LOCK_VERIFY_STAGE1.txt
sha256sum protocol/LOCK_E38v2.json results/$RUN_ID/SCHEDULE.json protocol/ANALYSIS_PLAN_E38v2.txt | tee $R/LOCK_HASHES_STAGE1.txt
python3 -c "import json;l=json.load(open('protocol/LOCK_E38v2.json'));print({k:l.get(k) for k in ('status','schedule_seed','run_id','schedule_sha256','analysis_plan_sha256','lock_sha256')}); print(l['environment'])"
