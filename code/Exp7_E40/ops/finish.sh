#!/usr/bin/env bash
W=$HOME/e40_20260926; cd $W; R=results/a20260926
K="nice kubectl --kubeconfig $W/kubeconfig"
bash ops/collect.sh a20260926 $W/protocol/LOCK_E40.json > ops/collect_main.log 2>&1; echo "collect rc=$?"; tail -6 ops/collect_main.log
# cluster facts before deletion
az aks show -g mcb-experiments -n e40-aks -o json > ops/aks_show.json
$K get nodes -o wide > ops/nodes.txt; $K version -o json > ops/k_version.json
$K get pods -A -o wide > ops/pods_all.txt
$K get validatingwebhookconfiguration e40-action-revalidation -o yaml > ops/vwc_live.yaml
$K -n e40-system get pod e40-prepull -o jsonpath='{range .status.containerStatuses[*]}{.image}{" "}{.imageID}{"\n"}{end}' > ops/IMAGES.txt
# delete the cluster (detached) right away
(setsid nohup bash e40_ops.sh delete > ops/delete.log 2>&1 < /dev/null &)
echo "delete launched"
