#!/usr/bin/env bash
# wait for the lead's deletion to finish, then recreate (koreasouth D2as_v5 only) and set up smoke1
W=$HOME/e40_20260926; cd $W
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a ops/LOG_E40_OPERATIONS.txt; }
log "RECREATE: waiting for previous cluster deletion (lead cancelled at 17:09Z)"
for i in $(seq 1 80); do
  a=$(az aks list -g mcb-experiments --query '[].name' -o tsv)
  # the VM identity has no read on MC_ groups (Forbidden); the AKS delete operation removes MC_ before it completes
  g=$(az group exists -n MC_mcb-experiments_e40-aks_koreasouth 2>/dev/null || echo not_verifiable_forbidden); [ "$g" = true ] || g=false
  [ -z "$a" ] && [ "$g" = false ] && break
  sleep 20
done
log "RECREATE: previous cluster gone (aks list empty=$([ -z "$a" ] && echo yes), MC_ group exists=$g)"
[ -z "$a" ] && [ "$g" = false ] || { log "RECREATE: previous deletion not finished; abort"; exit 5; }
mkdir -p ops/attempt1 && mv -f ops/aks_create.json ops/CLUSTER_CREATE*_EPOCH ops/k_version.json ops/IMAGES.txt ops/attempt1/ 2>/dev/null
rm -rf render tokens
E40_COMBOS=koreasouth:Standard_D2as_v5 bash e40_ops.sh create || exit 6
bash ops/setup1.sh > ops/setup1b.log 2>&1; echo "setup rc=$?"
tail -12 ops/setup1b.log
