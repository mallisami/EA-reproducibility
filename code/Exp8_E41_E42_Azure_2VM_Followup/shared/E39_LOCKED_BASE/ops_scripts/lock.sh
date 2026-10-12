#!/bin/bash
# E39 LOCK (RUNBOOK section 6). Usage: lock.sh RUN_ID
set -euo pipefail
RUN_ID=$1
E=~/e39_20260926
R=$E/results/$RUN_ID
cd $E
[ -e protocol/LOCK_E39.json ] && { echo "protocol/LOCK_E39.json exists; refusing"; exit 2; }
SEED=$(python3 -c "
import secrets
bad = {20260912, 1772720255, 154080643, 424242, 3902601, 3939}
while True:
    s = secrets.randbelow(2**31 - 1) + 1
    if s not in bad:
        print(s); break")
echo "seed $SEED" | tee $R/SEED.txt
venv_runner/bin/python src/e39_runner.py --make-schedule $R/SCHEDULE.json --seed $SEED --run-id $RUN_ID
venv_runner/bin/python src/e39_runner.py --dry-run --schedule $R/SCHEDULE.json > $R/DRY_RUN.json
python3 -c "import json; d=json.load(open('$R/DRY_RUN.json')); print(d['counts_by_experiment'], d['total_trials'], d['runtime_estimate']['total_hours'], {k: v['hours'] for k, v in d['runtime_estimate']['per_experiment'].items()})"
python3 protocol/lock_hash_e39.py fill protocol/LOCK_TEMPLATE_E39.json protocol/LOCK_E39.json \
    --seed $SEED --run-id $RUN_ID --schedule results/$RUN_ID/SCHEDULE.json \
    --analysis-plan protocol/ANALYSIS_PLAN_E39.txt \
    --e38v2r-lock ~/e38v2r_20260926/protocol/LOCK_E38v2.json --probe-tools
python3 protocol/lock_hash_e39.py verify protocol/LOCK_E39.json | tail -3
sha256sum protocol/LOCK_E39.json $R/SCHEDULE.json protocol/ANALYSIS_PLAN_E39.txt src/goclient/bin/e39-goclient
python3 -c "import json; d=json.load(open('protocol/LOCK_E39.json')); print({k: d.get(k) for k in ('status','schedule_seed','run_id','lock_sha256','forbidden_seeds')})"
