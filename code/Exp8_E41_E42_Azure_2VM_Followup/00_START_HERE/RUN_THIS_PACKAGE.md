# Run this package — Azure two-Linux-VM configuration

Use **`00_START_HERE/AZURE_TWO_VM_QUICKSTART.md`** as the authoritative operator runbook.

This package contains only the two additional experiments selected to reduce the remaining empirical-scope reviewer risk:

- **E41 (primary):** two-host replication of A1 / A3 / A4d on two separate Azure Linux VMs.
- **E42 (secondary):** benign target-Pod churn and resourceVersion conflict-cost study. Run only after E41 is preserved.

The scientific schedules, trial counts, outcomes, and analysis rules are frozen before execution. Smoke/probe data are engineering validation only and are never pooled with main data.

## Required deployment

- VM-1: Ubuntu Server 24.04 LTS, >=2 vCPU, >=4 GiB RAM, >=20 GiB free disk, K3s server.
- VM-2: Ubuntu Server 24.04 LTS, >=2 vCPU, >=4 GiB RAM, >=20 GiB free disk, K3s worker.
- same Azure VNet; K3s traffic over private IP only.
- different Availability Zones are recommended when available.
- TCP 6443 worker->server, UDP 8472 node<->node, TCP 10250 node<->node.
- do not expose UDP 8472 publicly.

## Execution order

1. `bash VERIFY_PACKAGE.sh` on both VMs.
2. `bash 01_ENVIRONMENT/shared/00_prepare_ubuntu.sh` on both VMs.
3. `bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh SERVER <SERVER_PRIVATE_IP>` on VM-1.
4. `bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh WORKER <WORKER_PRIVATE_IP>` on VM-2.
5. Install K3s server on VM-1.
6. Join VM-2 as worker using the printed token and VM-1 private IP.
7. Finalize the cluster and bootstrap Python on VM-1.
8. Run `bash ops/run_e41.sh smoke` on VM-1.
9. If smoke passes, run `bash ops/run_e41.sh main`.
10. Preserve E41, then run E42 smoke and main.
11. Archive the entire `runtime/` tree before deleting Azure VMs.

For exact commands and collection instructions, read `00_START_HERE/AZURE_TWO_VM_QUICKSTART.md`.
