#!/bin/bash
# E38v2 RERUN: RUNBOOK section 4 verification probes + runner-identity probes + running-Pod probe.
# Usage: probes.sh RUN_ID   (pod-event recorder must already be running for RUN_ID)
set -uo pipefail
RUN_ID=${1:?}
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
NS=e38v2-$RUN_ID
R=$E/results/$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
RESET=system:serviceaccount:e38v2-system:e38v2-reset
IMG=registry.k8s.io/pause:3.10
date -u +%FT%T.%NZ | tee $R/PROBES_START.txt
for p in probe-deny probe-allow probe-reset; do
  k -n $NS run $p --image=$IMG --image-pull-policy=Never --restart=Never \
    --labels=e38-managed=true,e38v2-webhook=enforce >/dev/null; done
k -n $NS run probe-nolabel --image=$IMG --image-pull-policy=Never --restart=Never --labels=e38-managed=true >/dev/null
k -n $NS wait --for=condition=Ready pod/probe-deny pod/probe-allow pod/probe-reset pod/probe-nolabel --timeout=120s
k -n $NS get pods -o wide
echo "== 4.3 invalid app_policy, labelled RUNNING pod, admin DELETE -> expect DENY"
k -n $NS patch configmap application-policy --type=merge -p '{"data":{"allowed":"false"}}'
k -n $NS delete pod probe-deny --wait=false; echo "exit=$? (expect non-zero)"
k -n $NS get pod probe-deny -o jsonpath='{.metadata.name} phase={.status.phase} deletionTimestamp={.metadata.deletionTimestamp}{"\n"}'
echo "== 4.4 invalid, unlabelled pod -> expect allowed, no webhook call"
k -n $NS delete pod probe-nolabel --wait=false; echo "exit=$? (expect 0)"
echo "== 4.5 invalid, reset identity -> exempt"
k -n $NS delete pod probe-reset --wait=false --as=$RESET; echo "exit=$? (expect 0)"
echo "== 4.6 valid -> ALLOW (kubelet final delete, if any, is exempt: system:node:*)"
k -n $NS patch configmap application-policy --type=merge -p '{"data":{"allowed":"true"}}'
k -n $NS delete pod probe-allow --wait=true; echo "exit=$? (expect 0)"
k -n $NS delete pod probe-deny --wait=true; echo "exit=$? (expect 0)"
echo "== 4.7 other predicates"
for spec in 'response-scope allowed' 'evidence-a value'; do set -- $spec
  k -n $NS run probe-x --image=$IMG --image-pull-policy=Never --restart=Never \
    --labels=e38-managed=true,e38v2-webhook=enforce >/dev/null
  k -n $NS wait --for=condition=Ready pod/probe-x --timeout=60s >/dev/null
  k -n $NS patch configmap $1 --type=merge -p "{\"data\":{\"$2\":\"false\"}}" >/dev/null
  if [ $1 = evidence-a ]; then
    k -n $NS patch configmap evidence-c --type=merge -p '{"data":{"value":"false"}}' >/dev/null
    k -n $NS patch configmap evidence-e --type=merge -p '{"data":{"value":"false"}}' >/dev/null
  fi
  k -n $NS delete pod probe-x --wait=false; echo "$1: EXPECT DENY exit=$?"
  for c in evidence-a evidence-c evidence-e; do k -n $NS patch configmap $c --type=merge -p '{"data":{"value":"true"}}' >/dev/null; done
  k -n $NS patch configmap response-scope --type=merge -p '{"data":{"allowed":"true"}}' >/dev/null
  k -n $NS delete pod probe-x --wait=true; echo "$1: EXPECT ALLOW exit=$?"
done
k -n $NS delete pod --all --as=$RESET --wait=true
echo "== runner-client probes (Audit-Id join, RV/time order, A3 deny/allow via runner identities; running target Pods)"
$E/venv_runner/bin/python ~/e38v2r_ops/probe_client.py $E $RUN_ID $R/PROBE_CLIENT_OPS.json
echo "== running-Pod probe (Running+Ready at gate time; container terminated after accepted DELETE)"
$E/venv_runner/bin/python ~/e38v2r_ops/running_pod_probe.py $E $RUN_ID $R/PROBE_RUNNING_POD.json 10 | tail -3
sleep 2
echo "== audit + webhook log checks"
sudo cat $E/results/_audit_host/audit.log | python3 ~/e38v2r_ops/probe_audit.py $NS $R/PROBE_CLIENT_OPS.json $E/results/_webhook_host/$RUN_ID/webhook_log.jsonl $E
date -u +%FT%T.%NZ | tee $R/PROBES_END.txt
