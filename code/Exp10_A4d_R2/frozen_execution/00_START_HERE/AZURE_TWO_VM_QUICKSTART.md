# Exact Azure two-VM run sequence

## Assumptions

- two disposable Ubuntu 24.04 Azure Linux VMs;
- each VM: >=2 vCPU, >=4 GiB RAM;
- server/package filesystem: >=25 GiB free before setup;
- same private VNet; worker can reach server private IP TCP/6443;
- this confirmation uses a **fresh K3s cluster**.

Copy the **same ZIP** to both VMs and extract it.

## 1. Verify the package on both VMs

```bash
cd EA_TOCTOU_A4D_AUDIT_SAFE_CONFIRMATION_20261001
bash VERIFY_PACKAGE.sh
bash 01_ENVIRONMENT/shared/00_prepare_ubuntu.sh
```

Expected final line: `PACKAGE VERIFICATION PASS`.

## 2. If reusing the disposable VMs from E41, remove the old K3s cluster

On the **worker** only:

```bash
CONFIRM_RESET=YES bash 01_ENVIRONMENT/agent/00_reset_existing_k3s_agent.sh
```

On the **server** only:

```bash
CONFIRM_RESET=YES bash 01_ENVIRONMENT/server/00_reset_existing_k3s_server.sh
```

Do not run these reset scripts on a machine containing any non-experiment workload.

## 3. Record Azure VM preflight

Substitute the actual Azure private IPs.

Server:

```bash
bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh SERVER <SERVER_PRIVATE_IP>
```

Worker:

```bash
bash 01_ENVIRONMENT/shared/00_azure_vm_preflight.sh WORKER <WORKER_PRIVATE_IP>
```

## 4. Install fresh K3s server with audit-safe retention

Server:

```bash
bash 01_ENVIRONMENT/server/01_install_k3s_server.sh <SERVER_PRIVATE_IP>
```

The script prints the K3s node token. Copy it securely to the worker terminal; do not paste it into package files or chat.

## 5. Join the worker

Worker:

```bash
bash 01_ENVIRONMENT/agent/02_install_k3s_agent.sh <SERVER_PRIVATE_IP>
```

Enter the token when prompted.

## 6. Finalize the two-host cluster

Server:

```bash
bash 01_ENVIRONMENT/server/03_finalize_cluster.sh
```

This command refuses the cluster unless there are exactly two Ready nodes with distinct InternalIPs and the running API server exposes all three required audit-retention flags.

## 7. Install frozen Python environments

Server only:

```bash
bash 01_ENVIRONMENT/shared/04_bootstrap_python.sh
```

This also runs the bounded offline regression suites and package verification.

## 8. Mandatory 26-trial smoke

Server only:

```bash
bash ops/run_a4d.sh smoke
```

A pass requires all of the following:

- 26/26 scheduled smoke records;
- no infrastructure error;
- live stale-RV/current-RV A4d probe passes;
- continuous audit stream captures the probe responseObjects;
- final evidence gate validates **100% of required writer and DELETE Audit-IDs**;
- analysis completes.

The command creates `runtime/SMOKE_PASS_A4D` only after these gates pass.

**Do not run main if smoke fails. Send the smoke runtime bundle back instead.**

## 9. Main A4d confirmation — 985 trials

Only after smoke passes:

```bash
bash ops/run_a4d.sh main
```

The main command refuses to start without the smoke PASS marker.

A completed main run is not called complete merely because 985 trials executed. Before analysis, `AUDIT_EVIDENCE_COMPLETENESS.json` must report `"PASS": true` and confirm every required primary audit record.

## 10. Produce a safe return bundle

After smoke or main:

```bash
bash ops/make_return_bundle.sh a4dcr-r2-20261001
```

For smoke use `a4dcs-r2-20261001` instead.

Upload the resulting `RETURN_A4D_*.tar.gz` and `.sha256`. The return bundle excludes ServiceAccount tokens and TLS private keys.
