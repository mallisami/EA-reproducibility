#!/usr/bin/env bash
set -euo pipefail
SERVER_IP=${1:?usage: $0 SERVER_PRIVATE_IP}
K3S_VERSION=${K3S_VERSION:-v1.37.0+k3s1}
command -v curl >/dev/null || { echo 'curl is required'; exit 2; }
if systemctl is-active --quiet k3s-agent 2>/dev/null; then
  echo 'Existing k3s-agent detected. Use a fresh/disposable cluster for this confirmation run.' >&2
  echo 'Run 01_ENVIRONMENT/agent/00_reset_existing_k3s_agent.sh with CONFIRM_RESET=YES first if appropriate.' >&2
  exit 3
fi
if [[ -z "${K3S_TOKEN:-}" ]]; then read -rsp 'K3s node token: ' K3S_TOKEN; echo; fi
[[ -n "$K3S_TOKEN" ]] || { echo 'empty K3S_TOKEN'; exit 2; }
if ! timeout 5 bash -c "</dev/tcp/${SERVER_IP}/6443" 2>/dev/null; then
  echo "Cannot reach ${SERVER_IP}:6443 from worker. Check Azure VNet/NSG/private IPs." >&2; exit 3
fi
echo "Installing K3s agent $K3S_VERSION as a4d-worker ..."
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="$K3S_TOKEN" sh -s - agent --node-name a4d-worker
sudo systemctl is-active --quiet k3s-agent
sudo k3s ctr images pull registry.k8s.io/pause:3.10
sudo k3s ctr images pull docker.io/library/python:3.12-slim
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; OUT="$ROOT/runtime/environment"; mkdir -p "$OUT"
{ date -u +%FT%TZ; sudo k3s --version; cat /etc/os-release; uname -a; lscpu; free -h; } > "$OUT/agent_environment.txt"
sha256sum "$OUT/agent_environment.txt" > "$OUT/agent_environment.sha256"
echo 'AGENT INSTALLED.'
