# E23 — F1 Fast-Rebinding Deadline-Feasibility Replication

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21 evidence. Nothing under
`02_locked_methodology_v8.1.1/` or elsewhere was modified.

## The gap this closes

The original E13 120-trial run's raw data (re-audited in the V15 manuscript
revision) shows that F1.1 and F1.2 — both frozen with a 5.0s
`commit_deadline_s` — had real live delete/recreate transition durations of
approximately **39–80 seconds**, because the backing resource was a full
Pod/StatefulSet and the measured duration included container scheduling,
image handling, and start latency, not just the identity-rebinding
mechanism itself. Those 40 trials were therefore structurally unable to
complete within their modeled deadline, so they could only demonstrate
functional stale-state detection, not deadline-bounded EA-TOCTOU
validation.

## The fix

E23 reproduces the **identical same-name-UID-rebinding mechanism** as
F1.1/F1.2 — an object is deleted and a replacement is created under the
same name with a new UID, and the response must remain bound to the
original UID — but hosts it on a **Kubernetes ConfigMap** instead of a
Pod. This isolates the identity-rebinding mechanism's timing from
container-runtime overhead while using the exact same causal structure
and the same real, unmodified `derive_critical_predicates()` /
`commit_components()` engine calls as every other live experiment in this
study. The real Kubernetes-assigned `metadata.uid` is the binding
evidence — not a synthetic data field — so this is a faithful identity
analog, not a different mechanism.

A genuine implementation bug was caught and fixed during this work: F1's
binding predicate is "does the current identity match *this decision's*
identity," which is a per-trial value (a fresh live UUID every run), not a
fixed constant the way the locked static F1.1/F1.2 scenario files encode
their one frozen UID ("U1"). The scenario file's placeholder value
("LIVE") is patched into a per-trial scenario copy at evaluation time
(`evaluate_gate()` in `scripts/run_e23.py`) rather than left as a literal
string comparison, which would (and initially did, in a first buggy run)
make every trial's binding predicate false regardless of condition. This
was caught immediately by the negative controls failing (`nc_stable`
should EXECUTE but was returning ABORT_REPLAN), fixed, and the run
repeated from a clean state.

## Protocol

`E23_PROTOCOL.json` (hash frozen in `E23_PROTOCOL.sha256`, verified by
`run_e23.py` at startup) fixes:
- Scenario `F1_FAST`, deadline 5.0s (identical to F1.1/F1.2's frozen
  deadline, so this is a directly comparable replication, not an
  easier test).
- 3 conditions, 20 repetitions each: `ea_attack` (delete+recreate
  `f1-target`, expect `ABORT_REPLAN`), `nc_stable` (no mutation, expect
  `EXECUTE`), `nc_irrelevant` (mutate an unrelated ConfigMap, expect
  `EXECUTE`) — mirroring E13's original NC-L1/NC-L2 negative-control
  design.
- Primary endpoint: `transition_duration_s`, measured as real wall-clock
  time for the attacker's delete+recreate HTTP calls to complete, not
  simulated.

## Results

All 60 trials completed with **0 mismatches** against expected decisions.
All 20/20 `ea_attack` trials were **deadline-feasible**
(`transition_duration_s` min 0.023s, max 0.232s, mean 0.057s — all
comfortably inside the 5.0s deadline, unlike F1.1/F1.2's 39–80s). Every
`ea_attack` trial correctly `ABORT_REPLAN`; every `nc_stable`/`nc_irrelevant`
trial correctly `EXECUTE`. Full per-trial data in `results/raw_trials.jsonl`;
aggregate in `results/summary.json`.

## Files

- `E23_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `manifests/` — namespace + the `f1-target`/`f1-irrelevant` ConfigMaps.
- `scenarios/F1_FAST.json` — the scenario; the real, unmodified
  `derive_critical_predicates()` returns a single-query plan (`p_binding`,
  cost 1.0) matching F1.1/F1.2's own single-predicate structure.
- `scripts/run_e23.py` — the frozen benchmark harness.
- `results/raw_trials.jsonl`, `results/summary.json`.

## What this experiment does and does not claim

- It does not claim to be a live quarantine of a real running workload —
  a ConfigMap is not a workload. It isolates and tests the *identity-
  rebinding timing mechanism* specifically, which is exactly what made
  F1.1/F1.2 deadline-infeasible in the original run.
- It does not add cost-efficiency evidence (both DCJI and FullRefresh
  query the same single predicate here, identical to F1.1/F1.2's own
  design) — that question is already answered by E21's multi-basis F5
  benchmark. E23's contribution is deadline feasibility, not cost.
- 20 repetitions test harness repeatability under one mechanism, not 20
  independent mechanisms.
