#!/bin/bash
# Interim oracle check on a SNAPSHOT (outside the run dir; nothing in the run dir is written). Usage: interim.sh RUN_ID
RUN_ID=$1
E=~/e39_20260926
R=$E/results/$RUN_ID
I=/tmp/e39_interim
rm -rf $I; mkdir -p $I
n=$(wc -l < $R/RAW_TRIALS.jsonl); head -n $n $R/RAW_TRIALS.jsonl > $I/RAW_TRIALS.jsonl
sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" > $I/audit.log
cp $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl $I/webhook_log.jsonl
m=$(wc -l < $R/pod_events.jsonl); head -n $m $R/pod_events.jsonl > $I/pod_events.jsonl
cd $E/analysis
../venv_analysis/bin/python oracle_e39.py --raw $I/RAW_TRIALS.jsonl --webhook $I/webhook_log.jsonl --audit $I/audit.log \
  --pod-events $I/pod_events.jsonl --out $I/oracle.csv | tail -1
rm -rf __pycache__
$E/venv_runner/bin/python ~/e39_ops/quality_gates.py $E $I $I/oracle.csv $I/QG.json > /dev/null
python3 - <<'PY'
import json
g = json.load(open('/tmp/e39_interim/QG.json'))
for k in ('n_trials', 'GATES', 'infra_error', 'audit_ids_joined', 'agreement_primary_secondary', 'primary_by_exp',
          'unknown_reasons', 'conflict_kinds', 'controls', 'disagreements', 'join_misses'):
    print(k, json.dumps(g.get(k), default=str)[:700])
PY
