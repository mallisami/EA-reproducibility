#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; OUT="$ROOT/runtime/environment"; mkdir -p "$OUT"
for i in $(seq 1 60); do n=$(sudo k3s kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"{n++} END{print n+0}'); [[ "$n" -eq 2 ]] && break; sleep 3; done
[[ "$(sudo k3s kubectl get nodes --no-headers | awk '$2=="Ready"{n++} END{print n+0}')" -eq 2 ]] || { echo 'Expected exactly two Ready nodes.' >&2; sudo k3s kubectl get nodes -o wide; exit 3; }
sudo k3s kubectl get node a4d-worker >/dev/null
sudo k3s kubectl label node a4d-worker a4d-role=workload --overwrite
sudo k3s kubectl label node a4d-server a4d-role=control --overwrite
mapfile -t NODE_IPS < <(sudo k3s kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' | sed '/^$/d')
[[ "${#NODE_IPS[@]}" -eq 2 && "${NODE_IPS[0]}" != "${NODE_IPS[1]}" ]] || { echo 'Two distinct node InternalIPs are required.' >&2; exit 4; }
sudo test -f /var/lib/rancher/k3s/server/logs/audit.log || { echo 'audit.log missing' >&2; exit 4; }
ARGS=$(sudo journalctl -u k3s --no-pager | grep 'Running kube-apiserver' | tail -1 || true)
for needle in '--audit-log-maxage=30' '--audit-log-maxbackup=20' '--audit-log-maxsize=1024'; do grep -Fq -- "$needle" <<<"$ARGS" || { echo "Missing live audit flag $needle" >&2; exit 4; }; done
FREE_KB=$(df -Pk "$ROOT" | awk 'NR==2{print $4}'); [[ "$FREE_KB" -ge 20971520 ]] || { echo "Need >=20 GiB free immediately before the run; free=${FREE_KB} kB" >&2; exit 4; }
{
 date -u +%FT%TZ; echo '--- k3s'; sudo k3s --version; echo '--- nodes'; sudo k3s kubectl get nodes -o wide --show-labels; echo '--- audit args'; echo "$ARGS"; echo '--- disk'; df -h "$ROOT" /var/lib/rancher/k3s/server/logs; echo '--- os'; cat /etc/os-release; echo '--- cpu'; lscpu; echo '--- memory'; free -h;
} > "$OUT/server_environment.txt"
sudo k3s kubectl get nodes -o json > "$OUT/nodes.json"
sha256sum "$OUT/server_environment.txt" "$OUT/nodes.json" > "$OUT/environment.sha256"
echo 'Cluster finalization PASS.'
sudo k3s kubectl get nodes -o wide
