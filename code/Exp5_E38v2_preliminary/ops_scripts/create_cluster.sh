#!/bin/bash
set -uo pipefail
E=~/e38v2_20260926
KC=$E/.kubeconfig_e38v2
cd $E
mkdir -p results/_audit_host results/_webhook_host
ls -ld results/_audit_host results/_webhook_host
if kind get clusters | grep -qx e38v2; then echo "e38v2 already exists"; exit 1; fi
date -u +%FT%TZ
kind create cluster --name e38v2 --config deploy/kind-config.yaml --kubeconfig $KC --wait 180s 2>&1 | tail -20
echo "rc=$?"
chmod 600 $KC
date -u +%FT%TZ
kind get clusters
kubectl config current-context
kubectl --kubeconfig $KC --context kind-e38v2 get nodes -o wide
kind load docker-image python:3.12-slim --name e38v2 2>&1 | tail -3
docker exec e38v2-control-plane crictl images | grep -E 'python.*3.12-slim'
docker exec e38v2-control-plane grep -E 'audit-(policy-file|log-path|log-maxsize|log-maxbackup|log-mode|log-format)' /etc/kubernetes/manifests/kube-apiserver.yaml
sudo ls -l $E/results/_audit_host/
free -m | head -2
