#!/bin/bash
# E39: preflight + create the dedicated kind cluster e39 (dedicated kubeconfig; never the shared one)
set -uo pipefail
E=~/e39_20260926
KC=$E/.kubeconfig_e39
OPS=~/e39_ops
cd $E
date -u +%FT%TZ
kind get clusters | tee $OPS/clusters_before.txt
docker ps --format '{{.Names}} {{.Status}}' | sort | tee $OPS/docker_before.txt
df -h ~ | tail -1; free -m | head -2; nproc; uptime
docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim
kind version; kubectl version --client 2>/dev/null | head -1
kubectl config current-context
mkdir -p results/_audit_host results/_webhook_host
chmod 777 results/_webhook_host
ls -ld results/_audit_host results/_webhook_host
if kind get clusters | grep -qx e39; then echo "e39 already exists"; exit 1; fi
kind create cluster --name e39 --config deploy/kind-config.yaml --kubeconfig $KC --wait 180s 2>&1 | tail -20
echo "rc=${PIPESTATUS[0]}"
chmod 600 $KC
date -u +%FT%TZ
kind get clusters
kubectl config current-context
kubectl --kubeconfig $KC --context kind-e39 get nodes -o wide
echo "== import python:3.12-slim into the e39 node (docker save | ctr import; no pull)"
docker save python:3.12-slim -o /tmp/e39_py312slim.tar
docker exec -i e39-control-plane ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs --platform linux/amd64 - < /tmp/e39_py312slim.tar
echo "import rc=$?"
rm -f /tmp/e39_py312slim.tar
docker exec e39-control-plane crictl images | grep -E 'pause|python'
docker exec e39-control-plane grep -E 'audit-|- --v=|vmodule' /etc/kubernetes/manifests/kube-apiserver.yaml
docker exec e39-control-plane grep -E 'containerLogMax' /var/lib/kubelet/config.yaml
kubectl --kubeconfig $KC --context kind-e39 describe node e39-control-plane | grep -iE 'taints|pods:'
free -m | head -2
