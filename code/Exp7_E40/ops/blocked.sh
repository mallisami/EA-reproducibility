#!/usr/bin/env bash
cd ~/e40_20260926
grep -q BLOCKED ops/LOG_E40_OPERATIONS.txt || echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) BLOCKED: az aks create rejected (BadRequest): Standard_B2s not allowed for AKS in this subscription in southeastasia; no resources created. Stopping per protocol; awaiting user decision on SKU/region." >> ops/LOG_E40_OPERATIONS.txt
echo "aks clusters in RG: $(az aks list -g mcb-experiments --query '[].name' -o tsv | wc -l)"
echo "e40 resources in RG: $(az resource list -g mcb-experiments --query "[?contains(name,'e40')].name" -o tsv | wc -l)"
tail -4 ops/LOG_E40_OPERATIONS.txt
