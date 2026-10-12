#!/usr/bin/env bash
# collect.sh RUN_ID : copy /out/RUN from the runner pod + webhook log, then run oracle/analysis on the VM
set -uo pipefail
W=$HOME/e40_20260926; RUN=$1
K="nice kubectl --kubeconfig $W/kubeconfig -n e40-system"
P=$($K get pod -l app=e40-runner -o jsonpath='{.items[0].metadata.name}')
WP=$($K get pod -l app=e40-webhook -o jsonpath='{.items[0].metadata.name}')
$K exec $P -c runner -- sh -c "touch /out/$RUN/PODWATCH_STOP; python -c \"import os
for p in os.listdir('/proc'):
    try:
        if p.isdigit() and b'e40_podwatch' in open('/proc/%s/cmdline' % p, 'rb').read(): os.kill(int(p), 15)
    except OSError: pass\"; true"
D=$W/results/$RUN; mkdir -p $D
$K exec $P -c runner -- tar czf - -C /out $RUN > $D/pod_out.tgz && tar xzf $D/pod_out.tgz -C $W/results/
$K exec $P -c runner -- tar czf - -C /out probes > $W/results/probes.tgz 2>/dev/null && tar xzf $W/results/probes.tgz -C $W/results/ || true
$K exec $WP -- cat /var/log/e40-webhook/$RUN/webhook_log.jsonl > $D/webhook_log.jsonl 2>/dev/null || true
$K logs $WP > $D/webhook_stdout.log 2>&1 || true
$K exec $P -c runner -- cat /e40/INCLUSTER_SHA256.txt > $D/INCLUSTER_SHA256.txt
: > $D/audit.log   # AKS: no apiserver audit log available (deviation D4) -> empty audit input
wc -l $D/RAW_TRIALS.jsonl $D/webhook_log.jsonl $D/pod_events.jsonl
cat $D/EXIT 2>/dev/null; tail -3 $D/runner.log
PY=$HOME/e39_20260926/venv_analysis/bin/python
A=$W/code/analysis
LOCK=${2:-}
if [ -z "$LOCK" ]; then   # smoke: temporary lock for the analysis input gate only
  python3 - "$D" "$RUN" <<'EOF'
import json,hashlib,sys
d,run=sys.argv[1:3]; s=json.load(open(d+'/SCHEDULE.json'))
json.dump({'status':'LOCKED-SMOKE-NOT-FOR-DATA','schedule_seed':s['schedule_seed'],'run_id':run,
 'schedule_sha256':hashlib.sha256(open(d+'/SCHEDULE.json','rb').read()).hexdigest()},open(d+'/LOCK_SMOKE.json','w'))
EOF
  LOCK=$D/LOCK_SMOKE.json
fi
rm -rf $D/analysis
(cd $A && nice $PY analyze_e39.py --run-dir $D --lock $LOCK --lock-root $W --analysis-plan $W/code/protocol/ANALYSIS_PLAN_E39.txt \
   --stage 1 --experiments A --design-check off --out $D/analysis > $D/analysis_stdout.log 2>&1); echo "analyze rc=$?"
tail -5 $D/analysis_stdout.log
