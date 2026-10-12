#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?usage: $0 RUN_ID}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; RD="$RT/render"; TOK="$RT/tokens"; RES="$RT/results"; DEPLOY_PY="$ROOT/.venv_deploy/bin/python"
[[ -x "$DEPLOY_PY" ]] || { echo 'Run 01_ENVIRONMENT/shared/04_bootstrap_python.sh first.' >&2; exit 2; }
[[ ! -e "$RD" ]] || { echo "Refusing to overwrite existing render dir $RD" >&2; exit 2; }
mkdir -p "$TOK" "$RES"; umask 077; k(){ sudo k3s kubectl "$@"; }
"$DEPLOY_PY" "$ROOT/ops/render_multihost.py" "$RUN_ID" "$RD"
k apply -f "$RD/namespace.yaml"; k apply -f "$RD/rbac.yaml"
k -n e39-system delete configmap e39-webhook-code --ignore-not-found
k -n e39-system delete secret e39-webhook-tls --ignore-not-found
k -n e39-system create configmap e39-webhook-code --from-file=server.py="$ROOT/shared/E39_LOCKED_BASE/webhook/server.py" --from-file=policy.py="$ROOT/shared/E39_LOCKED_BASE/webhook/policy.py"
k -n e39-system create secret tls e39-webhook-tls --cert="$RD/certs/tls.crt" --key="$RD/certs/tls.key"
k apply -f "$RD/webhook.yaml"; k -n e39-system rollout status deployment/e39-webhook --timeout=180s; k apply -f "$RD/webhook-config.yaml"
for x in a b c d e f; do k -n "e39-$RUN_ID" create configmap "evidence-$x" --from-literal=value=true --dry-run=client -o yaml | k apply -f -; done
k -n "e39-$RUN_ID" create configmap response-scope --from-literal=allowed=true --dry-run=client -o yaml | k apply -f -
k -n "e39-$RUN_ID" create configmap application-policy --from-literal=allowed=true --dry-run=client -o yaml | k apply -f -
k -n "e39-$RUN_ID" create configmap e38-epoch --from-literal=sequence=0 --dry-run=client -o yaml | k apply -f -
k -n "e39-$RUN_ID" label configmap --all e38-managed=true e39-managed=true --overwrite >/dev/null
for id in responder writer reset mirror load; do k -n e39-system create token "e39-$id" --duration=12h > "$TOK/$id.token"; done
chmod 600 "$TOK"/*.token; cp /var/lib/rancher/k3s/server/tls/server-ca.crt "$TOK/ca.crt"; chmod 600 "$TOK/ca.crt"; printf '%s\n' 'https://127.0.0.1:6443' > "$TOK/server.txt"
{
 date -u +%FT%TZ; echo '--- nodes'; k get nodes -o wide --show-labels; echo '--- webhook'; k -n e39-system get pod -l app=e39-webhook -o wide; echo '--- responder delete'; k auth can-i delete pods -n "e39-$RUN_ID" --as=system:serviceaccount:e39-system:e39-responder || true; echo '--- writer patch pod'; k auth can-i patch pods -n "e39-$RUN_ID" --as=system:serviceaccount:e39-system:e39-writer || true; echo '--- disk'; df -h "$ROOT" /var/lib/rancher/k3s/server/logs;
} > "$RES/setup_facts.txt"
W_NODE=$(k -n e39-system get pod -l app=e39-webhook -o jsonpath='{.items[0].spec.nodeName}')
[[ "$W_NODE" == 'a4d-worker' ]] || { echo "webhook on $W_NODE, expected a4d-worker" >&2; exit 5; }
FREE_KB=$(df -Pk "$ROOT" | awk 'NR==2{print $4}'); [[ "$FREE_KB" -ge 15728640 ]] || { echo "Need >=15 GiB free before run setup completes; free=${FREE_KB} kB" >&2; exit 5; }
echo "Run setup PASS: $RUN_ID"
