# E24 — Concurrent Multi-Query Acquisition Coherence

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E23 evidence. Uses a dedicated
namespace (`flashir-e24`) and its own ConfigMaps, not E21's, so already-
finalized E21 evidence is never touched.

## The gap this closes

Proposition 1's coverage guarantee assumes (A4) that a multi-query
commit-time refresh returns values from **one coherent acquisition
state** — i.e., that the justification-relevant state does not change
*during* the (non-atomic) sequence of physical reads. The manuscript
discloses that the prototype does not implement a general cross-resource
snapshot protocol. This experiment implements one concrete mechanism —
**version-fenced double-collect with bounded retry** — and empirically
tests whether it actually prevents delivering an incoherent (torn)
multi-query view, against a naive sequential multi-query acquisition with
no coherence check, under genuine concurrent mutation of the same three
physical resources DCJI selects in the F5 multi-basis scenario
(`obs-a`, `obs-c`, `obs-e`).

## Method

- **Naive sequential acquisition**: GET `obs-a`, then `obs-c`, then
  `obs-e`, one after another, with no check that the three values
  correspond to the same instant.
- **Fenced double-collect acquisition**: GET all three (round 1), record
  each `resourceVersion`, then immediately GET all three again (round 2).
  If every `resourceVersion` is unchanged between rounds, the round-1
  values are certified coherent and returned. If any changed, retry (up
  to 10 times); if still unresolved, return `UNKNOWN_COHERENCE` —
  fail-closed, matching this project's existing philosophy of failing
  safe rather than silently returning a potentially-wrong value.
- **Concurrent mutator**: a background thread continuously flips **all
  three resources simultaneously** (via a thread pool, so the three PATCH
  calls race each other) between `(true,true,true)` and
  `(false,false,false)`, logging every individual toggle's wall-clock
  timestamp and resulting `resourceVersion`.
- **Ground-truth coherence check**: for every acquired combination, the
  mutator's timestamped log (plus the known initial all-true state) is
  used to reconstruct each resource's exact value-over-time step function.
  A combination is **COHERENT** if and only if there exists at least one
  real instant at which every resource simultaneously held the value the
  acquisition returned for it; otherwise it is **INCOHERENT** — this
  check is applied identically to both methods and is independent of
  either acquisition implementation.

This design was deliberately hardened during development: an initial
version of the mutator flipped only one resource at a time, which — at
the read speeds this testbed achieves — never produced a naive incoherent
delivery in 200 attempts (an honest negative result, but not a useful
test of the mechanism). The mutator was changed to flip all three
resources concurrently, which does produce genuine tears, before the
protocol was frozen and the confirmatory run executed.

## Results

From `results/raw_trials.jsonl` (235 total attempts) and `results/summary.json`:

| Method | Attempts | Delivered | Incoherent (of delivered) | `UNKNOWN_COHERENCE` |
|---|---|---|---|---|
| Naive sequential | 200 | 200 | **17 (8.5%)** | 0 |
| Fenced double-collect | 35 | 0 | 0 (none delivered) | **35 (100%)** |

Under this sustained, adversarial, all-three-resources-at-once contention
level, the naive method delivered a torn (incoherent) view in 8.5% of
attempts — a real, measured, non-hypothetical failure of the coherence
assumption when it is not enforced. The fenced double-collect mechanism
delivered **zero incoherent views** — because under this contention level
it never successfully delivered *anything*: every attempt exhausted its
10 retries and correctly reported `UNKNOWN_COHERENCE` rather than risk
returning a torn combination. This is the exact safety property the
mechanism is meant to provide — it traded availability for correctness
under contention this severe, rather than silently failing coherence.

During early (unfrozen, exploratory) testing with a lighter contention
pattern — one resource toggled at a time rather than all three
concurrently — the fenced mechanism did successfully deliver coherent
results in the majority of attempts (with 0 incoherent deliveries there
either), while the naive method also happened not to produce any
observed tear at that lower contention level. That exploratory dataset
was superseded when the mutator was strengthened before freezing the
protocol, and is not included as formal evidence — only the frozen,
saved, fully-reproducible heavy-contention run above is reported as this
experiment's result.

## What this experiment does and does not claim

- It demonstrates that a naive multi-query acquisition **can** and, under
  measured real contention, **does** deliver an incoherent combined view
  — this is not a hypothetical concern.
- It demonstrates that a concrete, implementable fencing mechanism
  (version-checked double-collect) **never** delivered an incoherent view
  in this experiment, at either contention level tested.
- It does **not** claim the fenced mechanism is production-ready or
  optimally tuned — under the heavy-contention condition it never
  delivered a result at all within 10 retries, which would be
  operationally unacceptable in a real deployment; a real implementation
  would need adaptive retry budgets, exponential backoff, or a
  fall-through to a different strategy (e.g., a true atomic snapshot API
  where the platform provides one) rather than the fixed 10-retry cap
  used here for a bounded, reproducible experiment.
- It does not claim these two contention levels span the full space of
  real-world concurrent-mutation rates; they were chosen to (a) produce a
  measurable naive failure rate and (b) stress-test the fenced mechanism
  under adversarially severe contention.

## Files

- `E24_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `manifests.yaml` — namespace + the three ConfigMaps.
- `scripts/run_e24.py` — the frozen harness (naive/fenced acquisition,
  concurrent mutator, ground-truth coherence checker).
- `results/raw_trials.jsonl` — one row per acquisition attempt.
- `results/summary.json` — aggregate statistics.
