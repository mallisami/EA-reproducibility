#!/usr/bin/env bash
# Ubuntu 22.04/24.04 Azure VM, 4 vCPU / 16 GB.  Versions default to those recorded for the
# original campaigns; override with KIND_VERSION / KUBECTL_VERSION / NODE_IMAGE if needed.
set -euo pipefail
KIND_VERSION="${KIND_VERSION:-v0.33.0}"
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.37.0}"
NODE_IMAGE="${NODE_IMAGE:-}"            # e.g. kindest/node:v1.37.0 ; empty = kind default

sudo apt-get update -y
sudo apt-get install -y docker.io python3-venv curl
sudo usermod -aG docker "$USER" || true
if ! command -v kind >/dev/null; then
  curl -fsSLo kind "https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-amd64" \
    || { echo "kind ${KIND_VERSION} not found; using latest release"; curl -fsSLo kind "https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64"; }
  chmod +x kind; sudo mv kind /usr/local/bin/
fi
if ! command -v kubectl >/dev/null; then
  curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    || { S="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"; echo "kubectl ${KUBECTL_VERSION} not found; using ${S}"; curl -fsSLo kubectl "https://dl.k8s.io/release/${S}/bin/linux/amd64/kubectl"; }
  chmod +x kubectl; sudo mv kubectl /usr/local/bin/
fi

mkdir -p audit-logs runs
sudo kind delete cluster --name ea-followup >/dev/null 2>&1 || true
if ! sudo kind create cluster --config kind-config.yaml ${NODE_IMAGE:+--image "$NODE_IMAGE"}; then
  echo "Cluster with audit logging failed to start; creating one without audit logging."
  echo "audit logging unavailable on this kind/Kubernetes version" > runs/NO_AUDIT_LOG.txt
  sudo kind delete cluster --name ea-followup >/dev/null 2>&1 || true
  sudo kind create cluster --name ea-followup ${NODE_IMAGE:+--image "$NODE_IMAGE"}
fi
mkdir -p "$HOME/.kube"; sudo kind get kubeconfig --name ea-followup > "$HOME/.kube/config"

for img in registry.k8s.io/pause:3.10 busybox:1.36; do
  sudo docker pull "$img"; sudo kind load docker-image "$img" --name ea-followup
done

kubectl get ns ea-followup >/dev/null 2>&1 || kubectl create ns ea-followup
until kubectl -n ea-followup get serviceaccount default >/dev/null 2>&1; do sleep 1; done

python3 -m venv .venv
.venv/bin/pip install -q "kubernetes>=31"
kubectl version; kind version
echo "Setup done."
