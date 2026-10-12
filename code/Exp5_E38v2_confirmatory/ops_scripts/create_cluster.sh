#!/bin/bash
# E38v2 RERUN: preflight + create the dedicated kind cluster e38v2 (dedicated kubeconfig; never the shared one)
set -uo pipefail
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
cd $E
date -u +%FT%TZ
kind get clusters | tee /tmp/e38v2r_clusters_before.txt
docker ps --format '{{.Names}} {{.Status}}' | sort | tee /tmp/e38v2r_docker_before.txt
df -h ~ | tail -1; free -m | head -2; nproc; uptime
docker image inspect --format '{{.Id}} {{.RepoTags}}' kindest/node:v1.37.0 python:3.12-slim
kind version; kubectl version --client 2>/dev/null | head -1
sha256sum frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json
kubectl config current-context
mkdir -p results/_audit_host results/_webhook_host
ls -ld results/_audit_host results/_webhook_host
if kind get clusters | grep -qx e38v2; then echo "e38v2 already exists"; exit 1; fi
kind create cluster --name e38v2 --config deploy/kind-config.yaml --kubeconfig $KC --wait 180s 2>&1 | tail -20
echo "rc=${PIPESTATUS[0]}"
chmod 600 $KC
date -u +%FT%TZ
kind get clusters
kubectl config current-context
kubectl --kubeconfig $KC --context kind-e38v2 get nodes -o wide
docker exec e38v2-control-plane crictl images
docker exec e38v2-control-plane grep -E 'audit-|- --v=' /etc/kubernetes/manifests/kube-apiserver.yaml
docker exec e38v2-control-plane grep -E 'containerLogMax' /var/lib/kubelet/config.yaml
kubectl --kubeconfig $KC --context kind-e38v2 describe node e38v2-control-plane | grep -iE 'taints|pods:'
free -m | head -2
