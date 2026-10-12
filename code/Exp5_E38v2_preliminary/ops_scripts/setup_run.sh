#!/bin/bash
# Per-run setup (RUNBOOK section 3 + fixture ConfigMaps + section 5 tokens). Usage: setup_run.sh RUN_ID
set -euo pipefail
RUN_ID=${1:?run id}
E=~/e38v2_20260926
KC=$E/.kubeconfig_e38v2
NS=e38v2-$RUN_ID
R=$E/results/$RUN_ID
RD=$E/results/_render_$RUN_ID
k() { kubectl --kubeconfig $KC --context kind-e38v2 "$@"; }
cd $E
mkdir -p $R
date -u +%FT%T.%NZ
deploy/render.sh $RUN_ID $RD
deploy/gen_certs.sh $RD
cat $RD/RENDER_MANIFEST.txt
k apply -f $RD/namespace.yaml
k apply -f $RD/rbac.yaml
existed=0
if k -n e38v2-system get deployment e38v2-webhook >/dev/null 2>&1; then existed=1; fi
k -n e38v2-system delete configmap e38v2-webhook-code --ignore-not-found
k -n e38v2-system delete secret e38v2-webhook-tls --ignore-not-found
k -n e38v2-system create configmap e38v2-webhook-code --from-file=webhook/server.py --from-file=webhook/policy.py
k -n e38v2-system create secret tls e38v2-webhook-tls --cert=$RD/certs/tls.crt --key=$RD/certs/tls.key
k apply -f $RD/webhook.yaml
if [ $existed = 1 ]; then k -n e38v2-system rollout restart deployment/e38v2-webhook; fi
k -n e38v2-system rollout status deployment/e38v2-webhook --timeout=180s
sleep 3
k -n e38v2-system logs deploy/e38v2-webhook | grep serving | tail -1
for f in server.py policy.py; do
  if diff <(k -n e38v2-system get cm e38v2-webhook-code -o jsonpath="{.data.${f/./\\.}}") webhook/$f >/dev/null; then echo "$f identical"; else echo "$f DIFFERS"; exit 4; fi
done
k apply -f $RD/webhook-config.yaml
k get validatingwebhookconfiguration e38v2-delete-revalidation -o yaml | grep -E 'failurePolicy|timeoutSeconds|name: e38v2|kubernetes.io/metadata.name|e38v2-webhook: enforce'
echo "--- RBAC checks (expected: yes no yes no yes no no)"
k auth can-i delete pods -n $NS --as=system:serviceaccount:e38v2-system:e38v2-responder || true
k auth can-i patch configmap/response-scope -n $NS --as=system:serviceaccount:e38v2-system:e38v2-responder || true
k auth can-i patch configmap/response-scope -n $NS --as=system:serviceaccount:e38v2-system:e38v2-writer || true
k auth can-i delete pods -n $NS --as=system:serviceaccount:e38v2-system:e38v2-writer || true
k auth can-i get configmap/evidence-a -n $NS --as=system:serviceaccount:e38v2-system:e38v2-webhook || true
k auth can-i get configmap/e38-epoch -n $NS --as=system:serviceaccount:e38v2-system:e38v2-webhook || true
k auth can-i delete pods -n kube-system --as=system:serviceaccount:e38v2-system:e38v2-reset || true
echo "--- fixture ConfigMaps"
for x in a b c d e f; do k -n $NS create configmap evidence-$x --from-literal=value=true; done
k -n $NS create configmap response-scope --from-literal=allowed=true
k -n $NS create configmap application-policy --from-literal=allowed=true
k -n $NS create configmap e38-epoch --from-literal=sequence=0
k -n $NS label configmap --all e38-managed=true
echo "--- tokens + CA"
T=$E/.tokens_$RUN_ID
install -d -m 700 $T
for id in responder writer reset; do
  k -n e38v2-system create token e38v2-$id --duration=24h > $T/$id.token
done
chmod 600 $T/*.token
kind get kubeconfig --name e38v2 > $T/kubeconfig.yaml
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
ls -la $T
date -u +%FT%T.%NZ
