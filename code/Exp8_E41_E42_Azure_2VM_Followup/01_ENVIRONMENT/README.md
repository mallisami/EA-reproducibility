# Azure two-host environment

The frozen E41 deployment uses two **separate Microsoft Azure Linux VMs** connected through one private Azure VNet.

## Required VM profile

Both VMs:

- Ubuntu Server 24.04 LTS x86_64;
- >=2 vCPU;
- >=4 GiB RAM;
- >=20 GiB free disk;
- SSD-backed storage preferred;
- outbound HTTPS access to install K3s, Python packages, and pull the two experiment images.

For timing stability, a non-burstable 2-vCPU VM family is preferred. B-series is not prohibited, but the preflight records the Azure VM size so the environment is explicit.

## Topology

- `e41-server`: VM-1, K3s server/control plane and experiment driver.
- `e41-worker`: VM-2, K3s agent and workload node.
- experiment target Pods and webhook are pinned to `e41-worker`.
- K3s API/audit log remains on `e41-server`.

Different Azure Availability Zones are recommended when available. Zone placement strengthens physical fault-domain separation but is not used as a condition for including or excluding results.

## Network requirements

Use Azure **private IPs** between nodes. Permit at least:

- TCP 6443: worker -> server;
- UDP 8472: both directions for Flannel VXLAN;
- TCP 10250: both directions.

Do not expose UDP 8472 to the public Internet.

## Installation sequence

Follow `00_START_HERE/AZURE_TWO_VM_QUICKSTART.md` exactly.
