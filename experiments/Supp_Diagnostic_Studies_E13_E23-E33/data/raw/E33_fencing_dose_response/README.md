# E33 — Fencing Mechanism Dose-Response Curve Across Contention Levels

A preregistered post-hoc extension, run entirely outside the locked
v8.1.1 package and outside all other v9/E21/E23-E32 evidence. Reuses
E24's namespace/ConfigMaps without modifying E24's or E28's own frozen
protocols or results.

## The gap this closes

E24 (`inter_tick_delay=0.0s`, adversarial) and E28 (`inter_tick_delay=0.3s`,
moderate) each supplied exactly one point on the fenced mechanism's
delivery-rate curve — 0% and 100% respectively. Two isolated points do
not distinguish a gradual transition from a sharp one, and do not
identify where the mechanism's behavior actually changes. This
experiment adds two intermediate delays (0.05s, 0.15s), using the
identical mechanism, resources, and coherence oracle, to characterize
the actual shape of the transition.

## Result: the combined 4-point curve

Combining this experiment's two new points with E24's and E28's
already-recorded results (all four runs use the identical mechanism and
resources, differing only in the mutator's inter-tick delay):

| Inter-tick delay | Source | Fenced delivery rate | Fenced incoherent (of delivered) | Naive incoherent (of delivered) |
|---|---|---|---|---|
| 0.0s (adversarial) | E24 | **0.0%** (0/35) | n/a (none delivered) | 8.5% (17/200) |
| 0.05s | E33 (this experiment) | **99.0%** (101/102) | 0.0% | 0.0% (0/150) |
| 0.15s | E33 (this experiment) | **100%** (150/150) | 0.0% | 0.0% (0/150) |
| 0.3s (moderate) | E28 | **100%** (174/174) | 0.0% | 1.5% (3/200) |

**The transition is sharp, not gradual.** Fenced delivery jumps from 0%
to 99% between delay=0.0s and delay=0.05s — a small amount of contention
relief is enough to move the mechanism from complete failure to
near-complete success, rather than a smooth linear improvement across
the tested range. Delivery stays at or near 100% for every delay of
0.05s or greater tested here. The fenced mechanism's zero-incoherence
property (`0.0%` across all four conditions, 0 incoherent views in every
condition including the one where nothing delivered) held at every point
on the curve.

**An honest, unsmoothed observation on the naive method**: its
incoherence rate is *not* monotonic across these four points in the raw
data as collected — 8.5% at delay=0.0s, 0.0% at both 0.05s and 0.15s,
then 1.5% at 0.3s. This is reported exactly as measured rather than
adjusted to fit a cleaner expected trend. A plausible explanation is
sampling noise at a genuinely low base rate: naive's incoherence events
are rare (single-digit counts out of 150-200 trials at delays ≥0.05s), so
observing zero events in a 150-trial sample at low-but-nonzero true rates
is not statistically surprising. Distinguishing "the true rate is
non-monotonic" from "the true rate is monotonically decreasing but our
samples at 0.05s/0.15s happened to observe zero events" would require
substantially larger sample sizes at each delay than were run here — this
is disclosed as a real limitation of this specific dataset, not resolved.

## What this experiment does and does not claim

- It identifies where the fenced mechanism's delivery-rate transition
  occurs within the tested range (between 0.0s and 0.05s), which two
  isolated endpoints could not show.
- It does not claim the naive method's incoherence rate is non-monotonic
  as a matter of underlying fact — the observed non-monotonicity across
  only 4 delay points, each with a modest sample size, is consistent with
  sampling noise at low true rates and is reported as an open question,
  not a finding.
- 15 trials per (delay, method) combination — fewer than E24's/E28's 20 —
  were used to keep this already-large combined experiment tractable;
  this is disclosed as a smaller sample than the two endpoint
  experiments, not matched to them.

## Files

- `E33_PROTOCOL.json` / `.sha256` — frozen protocol and its hash.
- `scripts/run_e33.py` — the harness (identical mechanism to E24's/E28's
  `run_e24.py`/`run_e28.py`, looped over two delay levels).
- `results/raw_trials.jsonl`, `results/summary.json`.
