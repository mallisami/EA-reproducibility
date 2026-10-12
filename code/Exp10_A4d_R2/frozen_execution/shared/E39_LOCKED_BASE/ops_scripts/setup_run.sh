#!/bin/bash
# E39 per-run setup (RUNBOOK section 3 + fixture ConfigMaps + tokens). Usage: setup_run.sh RUN_ID
set -euo pipefail
RUN_ID=${1:?run id}
E=~/e39_20260926
KC=$E/.kubeconfig_e39
NS=e39-$RUN_ID
R=$E/results/$RUN_ID
RD=$E/results/_render_$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e39 "$@"; }
cd $E
mkdir -p $R
date -u +%FT%T.%NZ
deploy/render.sh $RUN_ID $RD
deploy/gen_certs.sh $RD
cat $RD/RENDER_MANIFEST.txt
k apply -f $RD/namespace.yaml
k apply -f $RD/rbac.yaml
existed=0
if k -n e39-system get deployment e39-webhook >/dev/null 2>&1; then existed=1; fi
k -n e39-system delete configmap e39-webhook-code --ignore-not-found
k -n e39-system delete secret e39-webhook-tls --ignore-not-found
k -n e39-system create configmap e39-webhook-code --from-file=webhook/server.py --from-file=webhook/policy.py
k -n e39-system create secret tls e39-webhook-tls --cert=$RD/certs/tls.crt --key=$RD/certs/tls.key
k apply -f $RD/webhook.yaml
if [ $existed = 1 ]; then k -n e39-system rollout restart deployment/e39-webhook; fi
k -n e39-system rollout status deployment/e39-webhook --timeout=180s
sleep 3
k -n e39-system logs deploy/e39-webhook | tail -2
for f in server.py policy.py; do
  if diff <(k -n e39-system get cm e39-webhook-code -o jsonpath="{.data.${f/./\\.}}") webhook/$f >/dev/null; then echo "$f identical"; else echo "$f DIFFERS"; exit 4; fi
done
k apply -f $RD/webhook-config.yaml
k get validatingwebhookconfiguration -o yaml | grep -nE 'operations|resources|failurePolicy|timeoutSeconds|- DELETE|- CREATE|- pods|- networkpolicies|e39-webhook: enforce|metadata.name'
echo "--- RBAC checks (runbook 3; expected: yes yes no yes no yes no no yes)"
SA=system:serviceaccount:e39-system
k auth can-i delete pods -n $NS --as=$SA:e39-responder || true
k auth can-i create networkpolicies -n $NS --as=$SA:e39-responder || true
k auth can-i patch configmaps -n $NS --as=$SA:e39-responder || true
k auth can-i patch pods -n $NS --as=$SA:e39-writer || true
k auth can-i delete pods -n $NS --as=$SA:e39-writer || true
k auth can-i patch pods -n $NS --as=$SA:e39-mirror || true
k auth can-i patch configmaps -n $NS --as=$SA:e39-mirror || true
k auth can-i list configmaps -n $NS --as=$SA:e39-load || true
k auth can-i patch configmaps -n e39-load --as=$SA:e39-load || true
echo "--- extra: reset may delete pods in kube-system? (expect no)"
k auth can-i delete pods -n kube-system --as=$SA:e39-reset || true
echo "--- fixture ConfigMaps"
for x in a b c d e f; do k -n $NS create configmap evidence-$x --from-literal=value=true; done
k -n $NS create configmap response-scope --from-literal=allowed=true
k -n $NS create configmap application-policy --from-literal=allowed=true
k -n $NS create configmap e38-epoch --from-literal=sequence=0
k -n $NS label configmap --all e38-managed=true e39-managed=true
k -n $NS get configmaps
echo "--- tokens + CA"
T=$E/.tokens_$RUN_ID
install -d -m 700 $T
for id in responder writer reset mirror load; do
  k -n e39-system create token e39-$id --duration=24h > $T/$id.token
done
chmod 600 $T/*.token
kind get kubeconfig --name e39 > $T/kubeconfig.yaml
chmod 600 $T/kubeconfig.yaml
python3 - "$T" <<'PY'
import base64, re, sys
t = sys.argv[1]
txt = open(t + "/kubeconfig.yaml").read()
ca = re.search(r"certificate-authority-data:\s*(\S+)", txt).group(1)
srv = re.search(r"server:\s*(\S+)", txt).group(1)
open(t + "/ca.crt", "wb").write(base64.b64decode(ca))
open(t + "/server.txt", "w").write(srv + "\n")
print("server", srv)
PY
chmod 600 $T/ca.crt
rm -f $T/kubeconfig.yaml
echo "--- whoami per identity"
for id in responder writer reset mirror load; do
  echo -n "$id: "; curl -s --cacert $T/ca.crt -H "Authorization: Bearer $(cat $T/$id.token)" -H 'Content-Type: application/json' \
    -X POST "$(cat $T/server.txt)/apis/authentication.k8s.io/v1/selfsubjectreviews" \
    -d '{"apiVersion":"authentication.k8s.io/v1","kind":"SelfSubjectReview"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"]["userInfo"]["username"])'
done
ls -la $T
date -u +%FT%T.%NZ
