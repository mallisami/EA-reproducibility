# Azure two-Linux-VM quick start

This is the execution path for the new empirical-scope experiments.

## Scientific purpose

- **E41 (primary):** run the decisive A1 / A3 / A4d comparison on a genuine two-host Kubernetes deployment.
- **E42 (secondary):** quantify benign target-object churn and resourceVersion conflict cost after E41 is preserved.

This package is prospectively frozen before the new main runs. Do not change arms, delays, schedules, trial counts, outcome definitions, or scientific code in response to results.

## Azure topology

Create **two separate Linux Azure VMs**:

| VM | K3s role | Minimum | Preferred |
|---|---|---|---|
| VM-1 | server/control plane + experiment driver | 2 vCPU, 4 GiB RAM, 20 GiB free disk | 2 vCPU, 8 GiB RAM, SSD |
| VM-2 | worker | 2 vCPU, 4 GiB RAM, 20 GiB free disk | 2 vCPU, 8 GiB RAM, SSD |

Use **Ubuntu Server 24.04 LTS x86_64** on both VMs. Keep both in the same Azure region and VNet. Different Azure Availability Zones are recommended when available, but are not a prerequisite for the scientific run.

For timing stability, prefer a non-burstable 2-vCPU VM family if your credits allow it. If a B-series VM is used, the package records the VM size and the manuscript must not treat absolute latency as portable.

K3s traffic must use **private IP addresses**. Public IPs, if present, are only for your SSH access.

## Azure network rules

Do not expose K3s networking to the public Internet. Between the two VM private addresses, allow:

- TCP 6443: worker -> server
- UDP 8472: both directions
- TCP 10250: both directions

Azure NSGs normally include virtual-network allow rules, but custom NSGs may override or narrow them. The worker installation script checks server TCP/6443 before joining. Cluster finalization will fail unless both K3s nodes become Ready.

## 1. Copy the identical ZIP to both VMs

Extract with a short path and enter the package directory.

On **both VMs**:

```bash
bash 01_ENVIRONMENT/shared/00_prepare_ubuntu.sh
```

Record each VM's Azure private IP from the Azure portal or:

```bash
hostname -I
```

Assume for the examples below:

```text
SERVER_PRIVATE_IP=10.0.0.4
WORKER_PRIVATE_IP=10.0.0.5
```

Use your actual private addresses.

## 2. Preflight both VMs

On VM-1/server:

```bash
bash VERIFY_PACKAGE.sh
bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh SERVER 10.0.0.4
```

On VM-2/worker:

```bash
bash VERIFY_PACKAGE.sh
bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh WORKER 10.0.0.5
```

Do not proceed if either preflight exits non-zero.

## 3. Install K3s server on VM-1

On VM-1:

```bash
bash 01_ENVIRONMENT/server/01_install_k3s_server.sh 10.0.0.4
```

The script prints a node token. Copy the token securely to VM-2. Do not add the token to the research package.

## 4. Join VM-2 as the K3s worker

On VM-2:

```bash
export K3S_TOKEN='PASTE_NODE_TOKEN_HERE'
bash 01_ENVIRONMENT/agent/02_install_k3s_agent.sh 10.0.0.4
unset K3S_TOKEN
```

The script checks TCP/6443 before installation and stops if VM-2 cannot reach VM-1 over the private network.

## 5. Finalize on VM-1

On VM-1:

```bash
bash 01_ENVIRONMENT/server/03_finalize_cluster.sh
bash 01_ENVIRONMENT/shared/04_bootstrap_python.sh
```

Expected cluster shape:

```text
e41-server   Ready   ...
e41-worker   Ready   ...
```

The nodes must have two distinct InternalIP addresses.

Copy the worker environment capture from VM-2 into VM-1's package before archiving final results:

```text
runtime/environment/agent_environment.txt
runtime/environment/agent_environment.sha256
runtime/environment/azure_worker_preflight.txt
runtime/environment/azure_worker_metadata.json
```

## 6. Mandatory E41 smoke

On VM-1 only:

```bash
bash ops/run_e41.sh smoke
```

Do not run the main study if smoke fails.

Preserve and inspect:

```text
runtime/e41s-20260930/results/PREFLIGHT_E41.json
runtime/e41s-20260930/results/PREFLIGHT_AUDIT_CHECK.json
runtime/e41s-20260930/results/E41_SUMMARY.json
runtime/e41s-20260930/results/RUN_STATS_E41.json
```

Smoke data are engineering validation and are never pooled with scientific main data.

## 7. E41 main

Only after a clean smoke:

```bash
bash ops/run_e41.sh main
```

Preserve the complete folder:

```text
runtime/e41r-20260930/
```

Do not rerun because the scientific result is unfavorable.

## 8. E42 smoke and main

Only after E41 has been copied safely off the VM or otherwise preserved:

```bash
bash ops/run_e42.sh smoke
bash ops/run_e42.sh main
```

Preserve:

```text
runtime/e42b-20260930/
```

## 9. Archive before deleting Azure VMs

On VM-1, after copying the worker environment files into `runtime/environment/`:

```bash
cd ..
tar -czf EA_TOCTOU_AZURE_2VM_RUNTIME_20260930.tar.gz \
  EA_TOCTOU_AZURE_2VM_E41_E42_20260930/runtime
sha256sum EA_TOCTOU_AZURE_2VM_RUNTIME_20260930.tar.gz \
  > EA_TOCTOU_AZURE_2VM_RUNTIME_20260930.tar.gz.sha256
```

Download both files before deleting the VMs.

## Frozen scientific trial counts

- E41 smoke: 78, excluded from analysis
- E41 main: 2,955
- E42 smoke: 18, excluded from analysis
- E42 main: 702
- total new scientific main trials: **3,657**

## What to send back

Upload the complete runtime archive and its SHA-256 file. Do not send only CSV summaries; the audit, pod-event, webhook, environment, and raw-trial records are required for manuscript integration.
