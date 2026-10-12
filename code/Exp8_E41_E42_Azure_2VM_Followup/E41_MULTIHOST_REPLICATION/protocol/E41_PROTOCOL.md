# E41 protocol — focused two-host replication

**Status:** prospectively frozen before main execution.  
**Role in manuscript:** external-validity/transportability strengthening of the central enforcement-boundary result.  
**Relationship to E39:** reuses the locked E39 gate, writer, DELETE, target-binding, record, and audit-oracle code; changes deployment and prospectively reduces the factor grid.

## 1. Research question

On a new two-host Kubernetes deployment, does the qualitative distinction between **revalidation freshness** and **target-bound enforcement** remain observable under the same classes of controlled invalidation?

The experiment is not designed to reproduce the exact exposure-window width from E39. Deployment-dependent timing is expected to change.

## 2. Deployment

- Two distinct Ubuntu 24.04 Linux VMs in Microsoft Azure on one private VNet. Different Availability Zones are recommended when available.
- K3s `v1.37.0+k3s1` / Kubernetes `v1.37.x`.
- One K3s server/control-plane host; experiment driver runs on this host and reaches the API on `https://127.0.0.1:6443` using a pinned CA and short-lived ServiceAccount tokens.
- One K3s agent/worker host.
- Target Pods and the admission-webhook Pod are pinned to the worker using `e41-role=workload`.
- The server API audit log uses the inherited E39 RequestResponse audit policy so the primary offline classifier has the same required fields.
- The deployment must be disposable and contain no production workload or personal data.

The server and worker are separately provisioned Azure VMs with distinct private addresses. The experiment is evidence for genuine multi-host execution; it is not an HA-control-plane, independent-cloud-provider, or production-site replication.

## 3. Arms

- `A1_dcji` — client-side DCJI gate followed by UID-preconditioned DELETE.
- `A3_dcji_webhook` — A1 plus validating admission recheck.
- `A4d_commit_direct` — target-resident authoritative validity state; DELETE carries both the UID and the resourceVersion observed by the target-object gate.

A2/FullRefresh is intentionally not repeated here. E41 is a focused replication of the decisive **client observation → admission observation → target-bound reject-capable boundary** contrast, not a repeat of every E38/E39 arm.

## 4. Invalidating conditions

Exactly the original E39-A conditions:

1. `async_scope`
2. `async_app_policy`
3. `async_evidence_last_literal`

The controlled writer is pre-armed and released relative to gate end using the inherited E39 writer implementation.

## 5. Fixed delay grid

`delta ∈ {0, 2, 5, 10, 20, 50, 100, 200} ms`.

The grid is frozen before execution and will not be extended because of observed results.

## 6. Sample size and controls

Per arm:

- 3 invalidating conditions × 8 delays × 39 trials = 936 asynchronous trials;
- 39 `no_write` controls;
- 10 `uid_rebind_control` trials.

Per arm total = 985.  
Three-arm total = **2,955 main trials**.

No adaptive top-up is allowed. The separate smoke run uses `n=1` per scheduled cell/control (**78 trials**) and is excluded from the scientific analysis.

## 7. Primary and corroborative outcomes

The inherited E39 offline analysis is used with the design-completeness check disabled only because E41 is a prospectively reduced subset of E39-A.

### Primary classification

Per-cell `P(INVALID_EXECUTED)` under the deployment-validated audit/resourceVersion ordering classifier, with Wilson 95% intervals.

### Required corroborative classification

ResponseComplete-oriented timing classification that does not depend on cross-kind resourceVersion numeric ordering. The earlier Pod-event/deletionTimestamp path is retained when available.

### Additional summaries

- exposure-window descriptors (`d*`, `W_A`) using the inherited definitions;
- target-precondition HTTP 409 correct-abort accounting;
- primary/secondary classifier agreement;
- no-write and UID-rebinding control behavior.

## 8. Replication interpretation

The manuscript integration must report the E41 result regardless of direction.

A result is **qualitatively consistent** with the prior mechanism interpretation if the new deployment again shows post-check exposure for one or both observation-only paths while the direct target-bound path shows stronger rejection at the protected boundary. This descriptor is secondary to the actual cell estimates and must never be used to exclude or rerun scientifically unfavorable data.

Exact delay thresholds, window areas, arm ordering, and conflict rates are deployment-specific and must not be required to match E39.

## 9. Pre-execution quality gates

Before main data:

1. exactly two Ready K3s nodes are present;
2. worker label `e41-role=workload` resolves to the agent node;
3. target and webhook Pods can be scheduled on the worker;
4. responder, writer, reset, and mirror identities resolve to the expected ServiceAccounts;
5. webhook invalid-state DELETE is denied and valid-state DELETE is allowed;
6. stale target resourceVersion DELETE returns HTTP 409, while current UID/resourceVersion DELETE succeeds;
7. audit RequestResponse events join to probe Audit-IDs and carry response resourceVersions;
8. the cross-object deployment diagnostic shows no observed contradiction between monotonically increasing numeric resourceVersion and audit ResponseComplete ordering in its fixed probe sequence;
9. pod-event recorder is running without GAP/ERROR records;
10. the smoke run completes without a scientific-data-quality blocker.

Probe and smoke data are diagnostic and are not pooled with main E41 trials.

## 10. Main-run engineering stop rules

- The inherited E39 runner stops after 20 consecutive infrastructure errors or >5% infrastructure errors after at least 100 newly executed trials.
- STOP-file termination is allowed for operator safety and is recorded.
- No scientific outcome triggers stopping.
- Process-only interruption can resume by trial ID if cluster/code/configuration are unchanged.
- Cluster restart/upgrade or code/configuration change invalidates pooling across the boundary; begin a fresh locked main run.

## 11. Analysis exclusions

No completed analyzable trial is removed because its result is unfavorable. `INFRA_ERROR` and pre-defined UNKNOWN/fail-closed states are reported under the inherited E39 rules. Smoke/probe trials are excluded by design because they have separate run IDs.

## 12. Evidence retained

Retain at minimum:

- frozen schedule and lock;
- raw trial JSONL;
- server audit log;
- pod-event log;
- webhook log;
- runner/session/stats logs;
- K3s version and two-node environment capture;
- analysis outputs and SHA-256 manifest.
