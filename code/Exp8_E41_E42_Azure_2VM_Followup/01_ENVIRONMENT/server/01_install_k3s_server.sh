#!/usr/bin/env bash
set -euo pipefail
SERVER_IP=${1:?usage: $0 SERVER_PRIVATE_IP}
K3S_VERSION=${K3S_VERSION:-v1.37.0+k3s1}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUDIT_SRC="$ROOT/E41_MULTIHOST_REPLICATION/protocol/audit-policy.yaml"
command -v curl >/dev/null || { echo 'curl is required'; exit 2; }
[[ -f "$AUDIT_SRC" ]] || { echo "missing $AUDIT_SRC"; exit 2; }

sudo mkdir -p /etc/rancher/k3s /var/lib/rancher/k3s/server/logs
sudo install -m 0644 "$AUDIT_SRC" /etc/rancher/k3s/audit-policy.yaml

echo "Installing K3s server $K3S_VERSION on $(hostname) ..."
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  sh -s - server \
    --node-name e41-server \
    --node-ip "$SERVER_IP" \
    --advertise-address "$SERVER_IP" \
    --tls-san "$SERVER_IP" \
    --disable traefik \
    --disable servicelb \
    --write-kubeconfig-mode 0600 \
    --kube-apiserver-arg audit-policy-file=/etc/rancher/k3s/audit-policy.yaml \
    --kube-apiserver-arg audit-log-path=/var/lib/rancher/k3s/server/logs/audit.log \
    --kube-apiserver-arg audit-log-maxage=7 \
    --kube-apiserver-arg audit-log-maxbackup=10 \
    --kube-apiserver-arg audit-log-maxsize=100

sudo systemctl is-active --quiet k3s
sudo k3s kubectl wait --for=condition=Ready node/e41-server --timeout=180s
sudo k3s kubectl version
sudo k3s --version

echo
echo 'SERVER INSTALLED.'
echo "Server private IP: $SERVER_IP"
echo 'Securely copy this node token to the agent; do not commit it into the experiment package:'
sudo cat /var/lib/rancher/k3s/server/node-token
