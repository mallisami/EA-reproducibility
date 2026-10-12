# E32 — Full Live Decision Trial on the Grace-Period-Fixed Real Pod Mechanism

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E23-E31 evidence. Combines E29's
root-cause diagnosis with E13's original live decision-gate methodology
against a byte-identical copy of the locked `f1_1_pod_benign.yaml`
manifest, unmodified.

## The gap this closes

E23 showed a ConfigMap-based surrogate mechanism is deadline-feasible.
E29 showed *why* the original Pod mechanism was not (a missing grace-
period fix, not scheduling or image-pull). Neither alone tested whether
the **actual F1.1 Pod mechanism**, with only the deletion call fixed,
produces correct live commit decisions within its original 5-second
deadline. This experiment closes that gap directly.

## Method

Uses the real, unmodified `derive_critical_predicates()` and
`commit_components()` engine calls, exactly as E13's and E23's harnesses
do, against a live Pod object that is a byte-identical copy of the locked
`f1_1_pod_benign.yaml` (`nodeName: flashir-nonexistent`,
`registry.k8s.io/pause:3.10.1`). The only behavioral change from the
original E13 harness is that the delete call uses
`gracePeriodSeconds: 0` instead of the default. 20 attack repetitions
(delete+recreate the Pod under the same name, expect `ABORT_REPLAN`) plus
20 stable and 20 irrelevant-mutation negative controls (expect
`EXECUTE`), mirroring E13's/E23's design.

## Result

All 60 trials completed with **0 mismatches** (`results/summary.json`).
All 20/20 attack trials were **deadline-feasible**
(`transition_duration_s` range [0.054, 1.947]s, mean 0.249s — every
single trial comfortably inside the original 5.0s deadline). The
planner's certificate (`p_binding`, `p_target_resolves`, cost 1.0)
matches F1.1's own single-predicate structure exactly.

**This is the most direct possible closure of the original F1
deadline-infeasibility finding**: not a substitute resource (E23), and
not timing alone without a decision gate (E29), but the real, unmodified
locked engine making correct live commit decisions against the actual
Pod-based mechanism, within the original deadline, after only a one-line
fix to how the delete call is issued.

## Relationship to E23 and E29

- **E23** demonstrated deadline feasibility exists for the identity-
  rebinding mechanism family in general, using a ConfigMap.
- **E29** diagnosed the exact root cause (default pod termination grace
  period) using the real Pod manifest, but measured only wall-clock
  timing, not decision correctness.
- **E32** combines both: real Pod, real fix, real decision gate,
  real deadline check — the complete story.

## What this experiment does and does not claim

- It does not claim `gracePeriodSeconds: 0` is safe for a real running
  workload (see E29's same caveat) — this Pod, like the original F1.1
  design, is deliberately never scheduled and never runs a container.
- It does not modify or challenge E13's original 120-trial result; it
  explains and resolves an already-correctly-reported limitation using
  the identical underlying mechanism.
- 20 repetitions per condition test repeatability of one mechanism, not
  independent attack mechanisms.

## Files

- `manifests/f1_1_pod_benign.yaml` — byte-identical copy of the locked
  manifest.
- `scenarios/F1_POD_FIXED.json` — scenario; predicate structure identical
  to E23's `F1_FAST.json`, with the target resource changed from
  ConfigMap to Pod.
- `scripts/run_e32.py` — the harness (real K8s Pod API calls: GET/POST/
  DELETE with `gracePeriodSeconds=0`, through the live proxy).
- `results/raw_trials.jsonl` (60 rows), `results/summary.json`.
