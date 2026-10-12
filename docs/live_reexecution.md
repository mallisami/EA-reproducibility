# L3: Live re-execution on a disposable kind cluster (not required for review)

L0-L2 verify package integrity and reproduce the audited paper-facing analyses and recorded-data summaries without any live cluster. L3 re-runs the Kubernetes experiments themselves. **It creates new data and cannot reproduce the published numbers bit-for-bit.** Timing-dependent outcomes (exposure curves, latencies) are host-specific (Section 7.5). What should transfer is the qualitative ordering: client gates are exposed, the validating webhook shifts exposure later, and direct commit binding (A4d) rejects covered target-state conflicts at the tested server-side action boundary.

**L3 was not re-executed during packaging.** The runbooks below are the ones the authors followed. Offline verification status is recorded in `docs/verification_summary.json`; live reruns are separate.

## Requirements
- A disposable Linux host (the authors used Ubuntu 24.04, 4 vCPU, 16 GB RAM). Run nothing else on it: other clusters on the same host changed background load during E39-D (deviation O5).
- Docker (29.1.3 was used), **kind v0.33.0**, **kubectl v1.37.0**, node image `kindest/node:v1.37.0` (digest in `environment/CONTAINER_IMAGES_AND_BUILD_INFO.txt`), Python 3.12 with the venvs from `environment/requirements/requirements-E38v2-*.txt` / `requirements-E39-E40-*.txt`, and **Go 1.27.1** for the E39-F client-go responder.
- Images used without pulls inside the node: `registry.k8s.io/pause:3.10` (target Pod) and `python:3.12-slim` (webhook). Import them with `docker save --platform linux/amd64 | ctr -n k8s.io images import` (deviation D3).
- About 3-6 h per experiment (E38v2 about 1 h of trials, E39 about 5 h), plus 1-2 GB of disk per run for audit and API-server logs.

## Procedure (common to E38v2 and E39)
1. Work in a copy of the code tree (`code/Exp5_E38v2_confirmatory`, `code/Exp6_E39`). Create the venvs (`venv_runner`, `venv_analysis`, `venv_deploy`) from the requirements files.
2. Offline test suites first: `src/` (pytest), `webhook/tests`, `deploy/tests/validate_deploy.py`, `analysis/run_tests.sh`, `protocol/tests`. The authors' counts for E39 were src 149 passed, webhook 41 OK, analysis + lock 117 passed (DEVIATIONS_E39 D1).
3. Create the cluster from `deploy/kind-config.yaml`. It sets the API-server audit policy (`deploy/audit-policy.yaml`) and, for the rerun and E39, the targeted `--v=3 --vmodule=store=6,round_trippers=6` logging. Use a dedicated kubeconfig (deviation D1). The authors' helper is `ops_scripts/create_cluster.sh`.
4. Render and apply the manifests: `deploy/render.sh`, `deploy/gen_certs.sh` (a fresh CA and webhook key, generated locally; no keys are shipped), `namespace.yaml`, `rbac.yaml`, `webhook.yaml`, `webhook-config.yaml`.
5. Probes (RUNBOOK section 4): webhook allow/deny, identities, Audit-Id join, resourceVersion-vs-timestamp ordering, running-Pod checks, and for E39 the A4d/mirror/NetworkPolicy/Go probes (`ops_scripts/probes.sh`, `e39_probes.py`).
6. Smoke run (`ops_scripts/smoke_sched.sh`, `smoke_analyze.sh`). Smoke data are not results.
7. **LOCK** with a new seed (`protocol/lock_hash.py` / `protocol/lock_hash_e39.py fill ...`; `verify` and `add-stage2` subcommands; see the docstring of the lock tool and `ops_scripts/lock*.sh`). Never reuse the published seeds; the tools refuse them.
8. Stage 1 (`ops_scripts/launch.sh` / `run_all.sh`), then the stage-2 top-up (`analysis/stage2_topup*.py`, `lock_hash* add-stage2`), then `ops_scripts/final.sh <RUN_ID>` (E39: `final.sh <RUN_ID> <W_A of A2 from your own E38v2 run>`).
9. Compare your `analysis_final/cells_*.csv` with the published ones qualitatively. Keep new-run results separate from the supplied canonical observations. The updated main-figure generator uses the documented presentation CSV inputs.

Exact step-by-step commands (the authors' runbooks; host-specific addresses redacted):
- E38v2: `experiments/Exp5_E38v2_Actuation_Exposure/Confirmatory/protocol/RUNBOOK_E38v2.txt`
- E39: `experiments/Exp6_E39_Commit_Bound_Enforcement/protocol/E39_RUNBOOK.txt` (design `E39_DESIGN_SPEC.txt`, analysis plan `ANALYSIS_PLAN_E39.txt`)
- E40 (AKS; needs an Azure subscription; costs money): `code/Exp7_E40/e40_ops.sh`, `experiments/Exp7_E40_AKS_Replication_Exploratory/protocol/E40_PROTOCOL.txt`. The E40 runner runs in-cluster from ConfigMaps. Rebuild `pylib.tgz` with `pip download -r environment/requirements/requirements-E39-E40-runner.txt` (it is withheld here).
- Earlier studies (E21/E25/E26/E34-E37, E13, E23-E33): the frozen runners in `experiments/*/data/raw/<study>/scripts/` need kind v0.32.0 and Kubernetes v1.36.1, as recorded in their protocol JSON files.

## Safety notes
Use only disposable clusters. The runners refuse non-loopback API endpoints (E38v2/E39) or anything other than the in-cluster service endpoint (E40). The webhook fails closed. Delete the cluster afterwards.

## Additional live replications

The recorded E41/E42 and A4d R2 results do not require a live rerun for package verification. For a new live replication:

- E41/E42 live-replication source: `code/Exp8_E41_E42_Azure_2VM_Followup/` and its `00_START_HERE/` runbook. It requires two distinct Ready K3s hosts and fresh credentials generated locally. Never reuse the removed tokens/keys from the private source runtime.
- A4d R2 live-replication source: `code/Exp10_A4d_R2/frozen_execution/`. Use fresh run IDs and locally generated certificates/tokens. The 26-trial smoke must pass before the 985-trial main schedule.

A new live run is a replication and should not overwrite the packaged recorded evidence.
