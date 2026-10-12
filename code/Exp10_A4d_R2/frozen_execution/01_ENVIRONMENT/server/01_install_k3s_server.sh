#!/usr/bin/env bash
set -euo pipefail
SERVER_IP=${1:?usage: $0 SERVER_PRIVATE_IP}
K3S_VERSION=${K3S_VERSION:-v1.37.0+k3s1}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUDIT_SRC="$ROOT/A4D_CONFIRMATION/protocol/audit-policy.yaml"
command -v curl >/dev/null || { echo 'curl is required'; exit 2; }
[[ -f "$AUDIT_SRC" ]] || { echo "missing $AUDIT_SRC"; exit 2; }
if systemctl is-active --quiet k3s 2>/dev/null; then
  echo 'Existing k3s server detected. Use a fresh/disposable cluster for this confirmation run.' >&2
  echo 'If these are the same disposable VMs, run 01_ENVIRONMENT/server/00_reset_existing_k3s_server.sh with CONFIRM_RESET=YES first.' >&2
  exit 3
fi
sudo mkdir -p /etc/rancher/k3s /var/lib/rancher/k3s/server/logs
sudo install -m 0644 "$AUDIT_SRC" /etc/rancher/k3s/audit-policy.yaml

echo "Installing K3s server $K3S_VERSION with audit-safe retention on $(hostname) ..."
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  sh -s - server \
    --node-name a4d-server \
    --node-ip "$SERVER_IP" \
    --advertise-address "$SERVER_IP" \
    --tls-san "$SERVER_IP" \
    --disable traefik \
    --disable servicelb \
    --write-kubeconfig-mode 0600 \
    --kube-apiserver-arg audit-policy-file=/etc/rancher/k3s/audit-policy.yaml \
    --kube-apiserver-arg audit-log-path=/var/lib/rancher/k3s/server/logs/audit.log \
    --kube-apiserver-arg audit-log-maxage=30 \
    --kube-apiserver-arg audit-log-maxbackup=20 \
    --kube-apiserver-arg audit-log-maxsize=1024
sudo systemctl is-active --quiet k3s
sudo k3s kubectl wait --for=condition=Ready node/a4d-server --timeout=180s
sudo k3s kubectl version
sudo k3s --version
# Verify that the running K3s process actually received the retention arguments.
ARGS=$(sudo journalctl -u k3s --no-pager | grep 'Running kube-apiserver' | tail -1 || true)
for needle in '--audit-log-maxage=30' '--audit-log-maxbackup=20' '--audit-log-maxsize=1024' '--audit-log-path=/var/lib/rancher/k3s/server/logs/audit.log'; do
  grep -Fq -- "$needle" <<<"$ARGS" || { echo "Running kube-apiserver is missing required flag $needle" >&2; exit 4; }
done
echo 'K3s audit-retention flags verified in the running server.'
echo 'Securely copy this node token to the worker; do not save it in the public result archive:'
sudo cat /var/lib/rancher/k3s/server/node-token
