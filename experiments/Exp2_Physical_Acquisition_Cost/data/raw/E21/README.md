# E21 — Independent Physical Revalidation Cost Benchmark

A protocol-frozen post-hoc extension, run and analyzed entirely outside the
locked v8.1.1 package and outside the existing v9 evidence (n=91 controlled
benchmark, E13's original 120 trials, E13's F5.LIVE multi-basis extension).
Nothing under `02_locked_methodology_v8.1.1/` or elsewhere in
`03_extended_study_v9/` was modified to produce this.

## Why this experiment exists

The existing E13 v9 extension (`../e13_live_multi/`) established that DCJI
selects 3 of 6 *logical* predicates against a real Kubernetes cluster, with
identical safety to FullRefresh. But that extension's 6 predicates were 6
labels on **one Pod object** — a single `kubectl get pod` call returns all
six at once, regardless of which predicates the planner selected. That
experiment is real and correctly analyzed, but it cannot, by construction,
demonstrate a *physical* (API-request-count / latency / payload) cost
saving, because DCJI's live query and FullRefresh's live query are the same
network call.

E21 fixes this by making each of the six predicates its own independent
Kubernetes API resource (a ConfigMap), each requiring its own HTTP GET
through a long-lived `kubectl proxy`. This converts the existing "DCJI
reads 3 logical predicates vs FullRefresh's 6" result into a "DCJI issues 3
HTTP requests, transfers roughly half the payload, and completes revalidation
faster than FullRefresh" result — the same underlying planner decision,
now measured at the layer a systems reviewer actually cares about.

## What changed vs. the flawed design

| | F5.LIVE (`e13_live_multi/`) | F5.PHYS (this experiment, E21) |
|---|---|---|
| Backing resource | 1 Pod, 6 labels | 6 independent ConfigMaps |
| DCJI's 3 queries | share 1 GET with FullRefresh's other 3 | require 3 independent GETs |
| Can show request-count/latency/payload difference? | No (both guards issue the same 1 GET) | Yes (guards issue provably different numbers of HTTP requests) |
| Query mechanism | `kubectl -n ... get pod ... -o json` subprocess | Direct HTTP GET via `kubectl proxy`, no subprocess in the timed region |

## Protocol

`E21_PROTOCOL.json` (hash frozen in `E21_PROTOCOL.sha256`, verified by
`run_e21.py` at startup — the run refuses to proceed if the protocol file
has been edited since freezing) fixes, before any trial was run:

- Scenario: `F5_PHYS` — same causal structure as the locked/v9 F5 family
  (`(A∧B)∨(C∧D)∨(E∧F)`), just re-hosted on 6 independent API resources.
- 3 conditions: `stable` (expect EXECUTE), `ea_attack` (expect
  ABORT_REPLAN — obs-a/obs-c/obs-e invalidated), `irrelevant_mutation`
  (expect EXECUTE — an unrelated ConfigMap changes).
- 2 acquisition modes: `sequential` (one GET after another) and `parallel`
  (`ThreadPoolExecutor`, all required GETs issued concurrently) — included
  specifically to preempt the "a real system would parallelize FullRefresh"
  objection.
- 2 load levels: `idle` (no background traffic) and `moderate` (~22
  requests/sec against an unrelated object, `background_load.py`), to test
  whether request-count reduction matters more under API contention.
- 20 warm-up paired trials per (load, mode, condition) cell, discarded (cold
  first-request latency is otherwise ~15-30x a warmed request — verified
  during pilot testing before the frozen run).
- 3 blocks × 100 measured paired trials per cell → 3,600 measured paired
  trials total across all 12 cells.
- Guard evaluation order (DCJI first vs. FullRefresh first) randomized per
  trial (seed `20260912`) and logged, to rule out request-order/cache
  effects.
- Every trial's decision is checked against its condition's expected
  decision (`dcji_matches_expected`, `full_matches_expected`), and every
  paired decision is checked for discordance — this is a safety criterion,
  not just a performance benchmark.

## Physical-request verification

`verify_e21.py` independently confirms, from `results/per_request_log.csv`
(one row per actual HTTP GET, with timestamp, resource, HTTP status,
latency, payload bytes, and `resourceVersion`) — not from the trial-level
summary — that DCJI issued exactly 3 requests and FullRefresh exactly 6 on
**every single trial**. This closes the exact loophole the request
identified in the prior F5.LIVE design: the request count is a directly
observed fact, not an inference from the planner's stated query cost.

## Files

- `E21_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `manifests/` — namespace + the 6 independent ConfigMaps (+ one unrelated
  `obs-irrelevant` object used for the irrelevant-mutation condition and
  the background-load generator).
- `scenarios/F5_PHYS.json` — the scenario; the real, unmodified
  `derive_critical_predicates()` returns `{flag_a, flag_c, flag_e}`, cost
  3.0, on this scenario (verified before the frozen run began).
- `scripts/preflight_e21.py` — environment capture + reachability checks,
  run before the timed benchmark.
- `scripts/background_load.py` — the L1 moderate-load generator.
- `scripts/run_e21.py` — the frozen benchmark harness.
- `scripts/analyse_e21.py` — protocol-aligned analysis: the frozen primary endpoint is paired refresh latency; it reports per-cell descriptive statistics, paired refresh-latency bootstrap CIs (10,000 resamples of complete pairs), secondary gate latency, and deadline-miss rates at the 5 prespecified thresholds.
- `scripts/verify_e21.py` — independent physical-request-count and
  safety-decision verification.
- `results/` — `raw_trials.jsonl` (one row per paired trial),
  `per_request_log.csv` (one row per actual HTTP GET),
  `cell_summaries.json` (written live as each cell finishes),
  `summary.json` / `paired_results.csv` / `latency_distribution.csv`
  (written by `analyse_e21.py`), `verification.json` (written by
  `verify_e21.py`).
- `logs/` — preflight output, proxy log, run log.

## Results

All 3,600 measured trials completed; `verify_e21.py` independently
confirmed, from the raw per-HTTP-GET log, **exactly 3 requests for DCJI and
exactly 6 for FullRefresh on every single trial** (0 exceptions), with **0
discordant decisions and 0 decisions mismatching the condition's expected
outcome**. The frozen primary endpoint is paired physical **refresh latency**.
After the V11.1 package re-audit aligned the analysis script to that endpoint,
DCJI had a lower mean paired refresh latency in **all 12 of 12**
(load × acquisition-mode × condition) cells, and every 10,000-pair bootstrap
95% CI for DCJI − FullRefresh lay entirely below zero (see
`results/summary.json`, `../../01_manuscript/tables/table2_e21_physical_cost_summary.csv`,
and `../../01_manuscript/figures/fig4_e21_refresh_latency_forest.png`).
Descriptively across the heterogeneous 3,600-trial set, DCJI used 3 rather
than 6 requests, transferred a mean 2,680 vs. 4,942 response-body bytes
(45.8% lower), and had median refresh latency 29.35 vs. 54.40 ms (46.1%
lower). These pooled percentages are descriptive only; the per-cell paired
refresh-latency intervals are the primary inferential evidence.

## V11.1 analysis-alignment note

The frozen protocol names `paired_refresh_latency_ms` as the primary
performance endpoint. The first post-run analysis script inadvertently
applied its paired bootstrap to total gate latency (refresh + policy
evaluation) instead. During consolidation, this mismatch was identified and
corrected in `scripts/analyse_e21.py` before manuscript finalization. The raw
trial log, per-request log, protocol file, protocol hash, and trial inclusion
set are unchanged. The correction does not change the conclusion: all 12
per-cell primary refresh-latency confidence intervals remain entirely below
zero. Gate latency remains reported as a prespecified secondary endpoint.

## An engineering fix, documented (not a methodology change)

The frozen run hit a transient `kubectl proxy` connection timeout at trial
1,373 of 3,600, inside the `idle/parallel/ea_attack` cell (block 2, rep
73/100) — a real infrastructure hiccup under concurrent HTTP connections
through a single `kubectl proxy` process on this machine, not a
data-generation bug. The first 1,200 trials (4 fully-completed cells:
`idle/sequential/{stable,ea_attack,irrelevant_mutation}`,
`idle/parallel/stable`) were retained as valid — verified to have exactly
300 trials each, matching the frozen protocol's per-cell trial count. The
harness (`run_e21.py`) was then extended with:
1. Bounded retries (5 attempts, exponential backoff) around every setup
   call (`set_observation`) and around each timed trial as a whole (a
   failed trial is fully discarded and retried, never partially recorded,
   so no timing measurement in the final dataset was contaminated by a
   retry).
2. A resume mode that reads any existing `raw_trials.jsonl`, identifies
   cells already at their full frozen trial count, and skips them
   entirely — re-running only the interrupted cell (`idle/parallel/ea_attack`,
   discarded and restarted cleanly from 0) and all subsequent cells.

Neither change altered the frozen protocol's conditions, trial counts,
random seed, or statistical plan (`E21_PROTOCOL.json` is unchanged and its
hash still verifies); both changes only made the harness robust to a
transient infrastructure failure. The resumed run completed the remaining
2,400 trials with 0 further retries logged in `results/infra_failures.jsonl`.
One documented, minor side effect: because the RNG (seed `20260912`) was
not re-seeded to replay the exact draws consumed by the interrupted run,
the guard-order randomization sequence for trials after the resume point is
not bit-identical to what an uninterrupted run would have produced — order
is still randomized and logged per trial, just not from the same point in
the seeded sequence a single continuous run would have reached.

## What this experiment does and does not claim

- It reuses the exact same F5 causal structure and the exact same,
  unmodified planner (`flashir_core.dcji.derive_critical_predicates`) as
  every other F5 result in this study — no new predicate-selection logic
  was written for E21.
- The 3,600 measured trials are repeated **performance observations** of
  the same underlying mechanism under different acquisition/load
  conditions — not 3,600 independent attack mechanisms. The safety claim
  (0 discordant decisions, 0 decisions mismatching the condition's
  expectation) is the confirmatory result; the latency/payload numbers are
  the performance result.
- "Response payload bytes" means literally `len(response_body)` from each
  HTTP GET — not network-wire bytes, which would additionally include HTTP
  headers and lower-layer (TCP/IP) overhead.
- Every number in the eventual report is computed from `raw_trials.jsonl`
  / `per_request_log.csv` by `analyse_e21.py` — nothing here is
  hand-typed or asserted before the run completed.
