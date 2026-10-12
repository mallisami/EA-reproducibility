#!/usr/bin/env bash
set -euo pipefail
SERVER_IP=${1:?usage: $0 SERVER_PRIVATE_IP}
K3S_VERSION=${K3S_VERSION:-v1.37.0+k3s1}
command -v curl >/dev/null || { echo 'curl is required'; exit 2; }
if [[ -z "${K3S_TOKEN:-}" ]]; then
  read -rsp 'K3s node token: ' K3S_TOKEN
  echo
fi
[[ -n "$K3S_TOKEN" ]] || { echo 'empty K3S_TOKEN'; exit 2; }

# Azure/private-network gate: the worker must reach the K3s API on the server private IP.
if ! timeout 5 bash -c "</dev/tcp/${SERVER_IP}/6443" 2>/dev/null; then
  echo "Cannot reach ${SERVER_IP}:6443 from worker. Check Azure VNet/NSG/private IPs before continuing." >&2
  exit 3
fi
echo "Private network check PASS: ${SERVER_IP}:6443 reachable"

echo "Installing K3s agent $K3S_VERSION as e41-worker ..."
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  K3S_URL="https://${SERVER_IP}:6443" \
  K3S_TOKEN="$K3S_TOKEN" \
  sh -s - agent --node-name e41-worker

sudo systemctl is-active --quiet k3s-agent
# Pre-pull only the two images required by the experiment. Failure here is explicit rather than deferred to a run.
sudo k3s ctr images pull registry.k8s.io/pause:3.10
sudo k3s ctr images pull docker.io/library/python:3.12-slim

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/runtime/environment"
mkdir -p "$OUT"
{
  date -u +%FT%TZ
  echo '--- k3s version'; sudo k3s --version
  echo '--- os-release'; cat /etc/os-release
  echo '--- uname'; uname -a
  echo '--- cpu'; lscpu
  echo '--- memory'; free -h
} > "$OUT/agent_environment.txt"
sha256sum "$OUT/agent_environment.txt" > "$OUT/agent_environment.sha256"

echo 'AGENT INSTALLED. Agent environment capture written under runtime/environment/.'
echo 'Return to the server host and run 03_finalize_cluster.sh.'
