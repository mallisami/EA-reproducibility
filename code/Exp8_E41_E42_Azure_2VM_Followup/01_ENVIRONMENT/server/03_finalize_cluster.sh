#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/runtime/environment"
mkdir -p "$OUT"
K='sudo k3s kubectl'

for i in $(seq 1 60); do
  n=$(sudo k3s kubectl get nodes --no-headers 2>/dev/null | awk '$2=="Ready"{n++} END{print n+0}')
  [[ "$n" -eq 2 ]] && break
  sleep 3
done
[[ "$(sudo k3s kubectl get nodes --no-headers | awk '$2=="Ready"{n++} END{print n+0}')" -eq 2 ]] || {
  echo 'Expected exactly two Ready nodes.' >&2; sudo k3s kubectl get nodes -o wide; exit 3; }

sudo k3s kubectl get node e41-worker >/dev/null
sudo k3s kubectl label node e41-worker e41-role=workload --overwrite
sudo k3s kubectl label node e41-server e41-role=control --overwrite

# Azure/two-host integrity gate: the two K3s nodes must report distinct InternalIP values.
mapfile -t NODE_IPS < <(sudo k3s kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\n"}{end}' | sed '/^$/d')
[[ "${#NODE_IPS[@]}" -eq 2 ]] || { echo "Expected two node InternalIPs; found ${#NODE_IPS[@]}" >&2; exit 4; }
[[ "${NODE_IPS[0]}" != "${NODE_IPS[1]}" ]] || { echo "Nodes share the same InternalIP; this is not a valid two-host run." >&2; exit 4; }

# Verify server-side audit logging is live.
sudo test -f /var/lib/rancher/k3s/server/logs/audit.log || { echo 'audit.log missing' >&2; exit 4; }

{
  date -u +%FT%TZ
  echo '--- k3s version'; sudo k3s --version
  echo '--- kubectl version'; sudo k3s kubectl version
  echo '--- nodes'; sudo k3s kubectl get nodes -o wide --show-labels
  echo '--- os-release'; cat /etc/os-release
  echo '--- uname'; uname -a
  echo '--- cpu'; lscpu
  echo '--- memory'; free -h
} > "$OUT/server_environment.txt"
sudo k3s kubectl get nodes -o json > "$OUT/nodes.json"
sha256sum "$OUT/server_environment.txt" "$OUT/nodes.json" > "$OUT/environment.sha256"

echo 'Cluster finalization PASS:'
sudo k3s kubectl get nodes -o wide
