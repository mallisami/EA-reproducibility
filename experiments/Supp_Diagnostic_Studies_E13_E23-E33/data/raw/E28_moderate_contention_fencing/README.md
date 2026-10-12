# E28 — Concurrent Acquisition Coherence Under Moderate (Realistic) Contention

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E23-E27 evidence. Reuses E24's
namespace/ConfigMaps without modifying E24's own frozen protocol or
results.

## The gap this closes

E24 tested the naive-vs-fenced acquisition mechanisms only under
sustained, adversarially maximal contention (all three resources flipped
concurrently as fast as HTTP round-trips allow). Under that condition,
the fenced double-collect mechanism never delivered a single result
within its 10-retry bound — it was fail-safe, but the reported evidence
left open whether it was ever *useful*, or only ever a refusal machine.
This experiment adds a moderate, bounded-rate contention condition (a
0.3s delay between mutator toggle rounds, roughly 3-6x slower than a
full fenced acquisition round) representing plausible occasional
configuration drift rather than adversarial saturation, using the
identical mechanism, resources, and ground-truth coherence oracle as E24.

## Result

From `results/raw_trials.jsonl` (374 total attempts) and `results/summary.json`:

| Method | Attempts | Delivered | Incoherent (of delivered) | Mean retries | Max retries |
|---|---|---|---|---|---|
| Naive sequential | 200 | 200 (100%) | **3 (1.5%)** | 0 | 0 |
| Fenced double-collect | 174 | **174 (100%)** | **0 (0%)** | 0.37 | 2 |

Under realistic contention, the fenced mechanism delivered a result in
**every single attempt** (174/174), with **zero incoherent views** and a
mean retry count under 0.4 (versus a fixed cost of 10/10 exhausted
retries for every attempt under E24's heavy-contention condition). The
naive method still produced a real, non-zero incoherence rate (1.5%) even
at this much lower contention level — confirming the coherence problem is
not solely an artifact of adversarial testing conditions.

**Combined with E24, this gives a complete picture rather than a single
data point**: the fenced double-collect mechanism is not merely a
fail-closed refusal machine — at contention rates a real deployment would
plausibly see, it delivers correct, coherent results with high
availability and low overhead, and it degrades gracefully to safe refusal
only when contention becomes severe enough that no non-atomic multi-read
strategy could safely proceed. The naive method, in contrast, has no such
degradation path: it always delivers something, and that something is
wrong at a rate that scales with contention (0/200 incoherent-of-200
delivered under low single-resource contention in early exploratory
testing noted in E24's README; 3/200 at this experiment's moderate rate;
17/200 under E24's heavy rate).

## What this experiment does and does not claim

- The 0.3s inter-tick delay is a chosen research parameter representing
  "moderate" contention, not a measured real-world configuration-drift
  rate for any specific production system; different real deployments
  would see different rates, and the mechanism's delivery rate would
  scale accordingly between this result and E24's heavy-contention
  result.
- It does not claim the fixed 10-retry bound is production-tuned — under
  this contention level it was never exhausted (max 2 retries observed),
  suggesting the bound has considerable headroom at realistic rates, but
  choosing an optimal bound for a real deployment remains future work.
- 20 trials per method test mechanism behavior repeatably at one
  contention level, not a full characterization of the delivery-rate
  curve as a function of contention rate; E24 and E28 together provide
  two points (heavy: 0% delivery; moderate: 100% delivery) on that curve,
  not the full curve.

## Files

- `E28_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `scripts/run_e28.py` — the harness (identical mechanism to E24's
  `run_e24.py`, with a bounded inter-tick delay added to the mutator).
- `results/raw_trials.jsonl`, `results/summary.json`.
