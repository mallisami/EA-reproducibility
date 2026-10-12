# E25 — Physical All-Predicate Multi-Cut Benchmark (Symmetric Threat Model)

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E22/E23/E24 evidence. Uses a
dedicated namespace (`flashir-e25`) and its own six ConfigMaps, not
E21's, so already-finalized E21 evidence is never touched.

## The gap this closes

The `08_revision_v15/audits/e22_threat_model_breadth_sensitivity.json`
audit established, using the deterministic planner alone (no live
infrastructure), that E21's "DCJI needs only 3 of 6 predicates" result is
specific to a *restricted* threat model in which the attacker always
invalidates the fixed set {A, C, E}. Under a *symmetric* threat model —
where the attacker may invalidate either member of each pair (A/B, C/D,
E/F) — the minimum certificate becomes all six predicates, and DCJI
correctly predicts no strict reduction. That was a valuable but purely
theoretical/offline finding. E25 physically re-derives it: does the real
planner, against a real Kubernetes cluster, actually select all six
predicates when the scenario's attacker model is symmetric, and do DCJI
and FullRefresh actually issue the same number of physical HTTP requests
in every one of the 8 minimal invalidating cuts?

## Method

- Scenario `F5_PHYS_SYM`: identical F5 policy
  `(A∧B)∨(C∧D)∨(E∧F)` to E21, but with attacker transitions for **all
  six** predicates (`cut_a` through `cut_f`), not just A/C/E. Six
  independent Kubernetes ConfigMaps (`obs-a`..`obs-f`), each requiring its
  own HTTP GET, exactly as in E21.
- Verified **before freezing the protocol** that the real, unmodified
  `derive_critical_predicates()` returns the full 6-predicate certificate
  (cost 6.0) on this scenario — matching E22's theoretical prediction
  exactly, now derived from a live-infrastructure-facing scenario file
  rather than only the abstract audit script.
- 9 conditions: `stable` (all true, expect `EXECUTE`) and all 8 minimal
  invalidating cuts identified by E22 (`cut_ACE`, `cut_ACF`, `cut_ADE`,
  `cut_ADF`, `cut_BCE`, `cut_BCF`, `cut_BDE`, `cut_BDF` — one member of
  each pair set false, expect `ABORT_REPLAN`), 10 repetitions each (90
  trials total).
- For every trial, both DCJI (using the planner's own 6-predicate
  certificate) and FullRefresh (also 6 predicates, by definition) query
  the live ConfigMaps and the real, unmodified `commit_components()`
  evaluates the decision.

## Results

All 90 trials completed. `results/summary.json`:
- **0/90 mismatches** against expected decisions, **0/90 discordant**
  decisions between DCJI and FullRefresh.
- **Physical request count was identical (6 = 6) in every single
  trial** — `mean_request_count_reduction: 0.0`,
  `trials_with_nonzero_reduction: 0`.
- The real planner's certificate for this scenario is confirmed to be all
  six predicates (`planner_cost: 6.0`), matching E22's theoretical result
  exactly.

This physically confirms, with 90 real live-Kubernetes trials rather than
only a deterministic planner call, that E21's cost-reduction result is a
genuine property of its specific restricted threat model and not an
artifact of the benchmark construction — and that DCJI correctly derives
**zero** reduction (not a wrong or degenerate answer) when the threat
model actually requires querying every predicate. A null result here is
the theoretically predicted, correct outcome, not a failed experiment.

## Files

- `E25_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `manifests.yaml` — namespace + 6 ConfigMaps.
- `scenarios/F5_PHYS_SYM.json` — the symmetric-threat scenario.
- `scripts/run_e25.py` — the frozen benchmark harness.
- `results/raw_trials.jsonl` (90 rows), `results/summary.json`.

## What this experiment does and does not claim

- It does not claim symmetric attacker mutability is the "correct" or
  more realistic threat model than E21's restricted one — both are
  legitimate, differently-scoped models. It claims that DCJI's behavior
  under each is exactly what the theory predicts.
- It does not re-test the deadline-discrimination, complexity-scaling, or
  concurrent-acquisition questions already covered by E12c/E14/E24; it is
  narrowly scoped to the cost-reduction claim's threat-model boundary.
- 10 repetitions per condition test harness/decision repeatability, not
  10 independent attack mechanisms.
