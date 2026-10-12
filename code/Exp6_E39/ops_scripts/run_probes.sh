#!/bin/bash
# E39 RUNBOOK section 4 probes. Usage: run_probes.sh RUN_ID [PARTS comma list]
set -uo pipefail
RUN_ID=${1:?}; PARTS=${2:-}
E=~/e39_20260926
KC=$E/.kubeconfig_e39
NS=e39-$RUN_ID
R=$E/results/$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e39 "$@"; }
cd $E
date -u +%FT%T.%NZ | tee -a $R/PROBES_START.txt
bash ~/e39_ops/podwatch.sh start $RUN_ID
free -m | head -2; uptime
$E/venv_runner/bin/python ~/e39_ops/e39_probes.py $E $RUN_ID $R/PROBES_E39${PARTS:+_$PARTS}.json ${PARTS:+--parts $PARTS}
if [ -z "$PARTS" ]; then
  echo "== kubelet exemption: admin DELETE with default grace of a labelled Pod (valid state) -> webhook allow;"
  echo "   the kubelet's final DELETE (system:node) must not reach the webhook"
  k -n $NS run probe-kubelet --image=registry.k8s.io/pause:3.10 --image-pull-policy=Never --restart=Never \
    --labels=e39-managed=true,e39-webhook=enforce >/dev/null
  k -n $NS wait --for=condition=Ready pod/probe-kubelet --timeout=90s
  k -n $NS delete pod probe-kubelet --wait=true; echo "exit=$?"
  sleep 2
  echo "webhook entries for probe-kubelet: $(grep -c '"probe-kubelet"' $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl)"
fi
sleep 2
F=$R/PROBES_E39${PARTS:+_$PARTS}.json
sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" | \
  python3 ~/e39_ops/e39_probe_audit.py $NS $F ${F%.json}_AUDIT.json
if [ -z "$PARTS" ]; then
  sudo sh -c "cat \$(ls -tr $E/results/_audit_host/audit-*.log 2>/dev/null) $E/results/_audit_host/audit.log" | \
    python3 -c "
import json,sys
ev=[json.loads(l) for l in sys.stdin if l.strip()]
d=[e for e in ev if (e.get('objectRef') or {}).get('name')=='probe-kubelet' and e['verb']=='delete' and e['stage']=='ResponseComplete']
for e in d: print('probe-kubelet delete by', e['user']['username'], 'code', (e.get('responseStatus') or {}).get('code'))
"
fi
k -n $NS delete pod,networkpolicy --all --as=system:serviceaccount:e39-system:e39-reset --wait=true
date -u +%FT%T.%NZ | tee -a $R/PROBES_END.txt
