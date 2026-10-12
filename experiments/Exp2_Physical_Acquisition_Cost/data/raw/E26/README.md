# E26 — Independent-Cluster Replication of E21's Primary Confirmatory Condition

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E23/E24/E25 evidence. Runs against a
**genuinely separate Kubernetes cluster** — a fresh `kind create cluster
--name flashir-e26` invocation, not a restart or reuse of `flashir-e13` —
through its own dedicated `kubectl proxy` on a distinct port (8002 vs
E21's 8001). E21's original cluster and data are completely untouched.

## The gap this closes

E21's manuscript-reported limitations section correctly restricts its
3,600-trial bootstrap confidence intervals to **within-testbed** repeated-
trial uncertainty: every one of those trials ran against one `kind`
cluster instance, one process environment, one long-lived proxy. This
experiment tests whether the qualitative finding — DCJI issues fewer
physical requests and completes commit-gate revalidation faster than
FullRefresh, with zero safety discordance — actually **replicates on an
independently created cluster**, rather than being an artifact specific
to the original cluster instance.

## A real infrastructure constraint, documented honestly

Creating a second full `kind`/Kubernetes control plane while
`flashir-e13` (used by E21/E23/E24/E25) and an unrelated project's
4-node `mape-cap-bplus` cluster were both running caused the new
cluster's `kubeadm init` to time out repeatedly on internal RBAC
bootstrap calls (`ClusterRoleBinding` POST requests exceeding their 10s
timeout) — a genuine resource-contention failure on this machine, not a
configuration bug. `flashir-e13` was stopped (its data was already saved
and independently verified, so this cost nothing) to free CPU/memory,
after which cluster creation for `flashir-e26` succeeded. `flashir-e13`
was restarted immediately after this experiment's trials completed. This
is reported because it is itself informative: on constrained hardware,
genuine multi-cluster replication requires either more resources or
serializing cluster lifecycles, which is exactly what was done here.

## Method

Scoped to E21's **primary confirmatory condition** only (idle load,
sequential acquisition) rather than the full 12-cell matrix, since this
is a replication check, not a new confirmatory benchmark: the same
`F5_PHYS` scenario, the same 3 conditions (`stable`, `ea_attack`,
`irrelevant_mutation`), 20 discarded warm-up pairs + 100 measured paired
trials per condition (300 measured trials total). The real, unmodified
`derive_critical_predicates()` was re-verified to return the identical
`{flag_a, flag_c, flag_e}` certificate (cost 3.0) on this new cluster
before running.

## Results

All 300 trials completed. `results/summary.json`:

| Metric | Value |
|---|---|
| Discordant decisions | 0 / 300 |
| Decisions mismatching expected outcome | 0 / 300 |
| Mean DCJI request count | 3.0 |
| Mean FullRefresh request count | 6.0 |
| Mean paired refresh-latency diff (DCJI − FullRefresh) | **−66.3 ms** |
| Bootstrap 95% CI | **[−134.3, −30.7]** (entirely below zero) |
| Median DCJI gate latency | 28.2 ms |
| Median FullRefresh gate latency | 59.8 ms |

Per-condition breakdown (all three negative, i.e. favoring DCJI, matching
E21's own idle/sequential cells in direction):

| Condition | Mean refresh diff (ms) | E21's original idle/sequential value |
|---|---|---|
| `stable` | −131.0 | −91.1 |
| `ea_attack` | −30.7 | −128.9 |
| `irrelevant_mutation` | −37.2 | −84.0 |

**The qualitative finding replicates**: on a completely independent
cluster instance, DCJI issued exactly the same reduced request count
(3 vs 6), safety was identical (0 discordant, 0 mismatched-expected), and
the paired refresh-latency advantage held in every condition with its
95% CI entirely below zero — the same conclusion E21 reached. The exact
magnitudes differ from E21's original cluster (as expected: different
container, different point in time, different exact hardware/scheduling
noise on the same physical machine), which is itself informative — it
shows the *direction and existence* of the effect is not an artifact of
one specific cluster instance, while the *magnitude* is expected to vary
across environments, exactly the caveat E21's own limitations section
already carried.

## What this experiment does and does not claim

- It replicates E21's primary confirmatory condition (idle/sequential, 3
  conditions) only — it does not repeat the full 12-cell load × mode
  matrix, parallel acquisition, or moderate background load. Those
  remain single-cluster evidence.
- It is one additional cluster instance, not "multiple cluster
  restarts/days/environments" in the fullest sense the request
  envisioned — genuine multi-day, multi-machine, or multi-cloud-provider
  replication remains further future work. It is, however, a real,
  independently-created cluster (fresh control plane, fresh etcd, fresh
  network), which is a materially stronger claim than re-running against
  the same container.
- The exact magnitude of the latency advantage is not claimed to
  generalize numerically across environments — only its direction and
  the safety/request-count findings are claimed to replicate.

## Files

- `E26_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `scenarios/F5_PHYS.json` — identical scenario to E21's.
- `physical_observations.yaml` — the 6+1 ConfigMaps, applied to the new
  cluster's `flashir-e21` namespace.
- `scripts/run_e26.py` — the frozen benchmark harness.
- `results/raw_trials.jsonl` (300 rows), `results/summary.json`.
