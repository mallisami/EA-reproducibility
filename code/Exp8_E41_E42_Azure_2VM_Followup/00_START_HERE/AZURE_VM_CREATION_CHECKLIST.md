# Azure VM creation checklist

Create the two VMs before running the package.

## VM-1 — server

- Image: Ubuntu Server 24.04 LTS x86_64
- vCPU: 2
- RAM: >=4 GiB; 8 GiB preferred
- Disk: >=30 GiB SSD preferred
- Network: same VNet as VM-2
- Private IP: record it; this is the address used by K3s
- Availability Zone: choose a zone if supported
- SSH: restrict to your own source IP where practical

## VM-2 — worker

Use the same OS and comparable VM size as VM-1.

- vCPU: 2
- RAM: >=4 GiB; 8 GiB preferred
- Disk: >=30 GiB SSD preferred
- same VNet as VM-1
- distinct private IP
- if possible, select a different Availability Zone from VM-1

## Networking

K3s cluster traffic must use private addresses. Ensure custom NSGs do not block:

| Protocol | Port | Source | Destination |
|---|---:|---|---|
| TCP | 6443 | VM-2 private IP | VM-1 private IP |
| UDP | 8472 | VM-1/VM-2 private IPs | VM-1/VM-2 private IPs |
| TCP | 10250 | VM-1/VM-2 private IPs | VM-1/VM-2 private IPs |

Do **not** add a public Internet rule for UDP 8472.

## VM family

A non-burstable 2-vCPU family is preferable for a timing-sensitive systems experiment. If Azure credit constraints require a B-series VM, use the same size for both VMs where possible and retain the preflight metadata; absolute latency remains deployment-specific in the manuscript.

## Before running E41

- both VM clocks report NTP synchronization;
- both VMs pass the package preflight;
- VM-2 reaches VM-1 private TCP/6443 after the server is installed;
- K3s reports exactly two Ready nodes with distinct InternalIP values;
- target/webhook placement gates report `e41-worker`.
