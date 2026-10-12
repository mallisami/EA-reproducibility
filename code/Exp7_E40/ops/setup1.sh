#!/usr/bin/env bash
set -euo pipefail
W=$HOME/e40_20260926; cd $W
k() { nice -n 10 kubectl --kubeconfig $W/kubeconfig "$@"; }
bash e40_ops.sh creds
k create namespace e40-system --dry-run=client -o yaml | k apply -f -
# prepull pod only (from runner-job.yaml, last document)
python3 - <<'EOF'
t=open('/home/azureuser/e40_20260926/k8s/runner-job.yaml').read().split('\n---\n')
open('/home/azureuser/e40_20260926/k8s/prepull.yaml','w').write(t[-1])
EOF
k apply -f k8s/prepull.yaml
for i in $(seq 1 60); do
  s=$(k -n e40-system get pod e40-prepull -o jsonpath='{range .status.containerStatuses[*]}{.imageID}{" "}{end}')
  n=$(echo $s | wc -w); [ "$n" -ge 2 ] && break; sleep 5; done
k -n e40-system get pod e40-prepull -o jsonpath='{range .status.containerStatuses[*]}{.image}{" "}{.imageID}{"\n"}{end}' | tee ops/IMAGES.txt
bash e40_ops.sh deploy smoke1
bash e40_ops.sh tokens
bash e40_ops.sh code_cms
bash e40_ops.sh job 972231175
k get pods -A -o wide | grep -v kube-system
k get nodes -o wide
