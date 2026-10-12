# FlashIR E11 — Telemetry Lag Robustness

## Semantics

At real commit time `t_e`, refresh-based defenses make their decision at the true wall
clock, but a query returns the latest state snapshot at:

`max(0, t_e - lag)`.

Thus **time is current but telemetry values are delayed**.

This distinction is important for TTLGuard: its age threshold is still evaluated at the
real commit time rather than being artificially reset by lagged telemetry.

B5 NativePrecondition is modeled as an atomic server-side commit condition and therefore
continues to evaluate the actual commit state rather than the delayed client telemetry.

## Template-level results

| Lag | B2 TTL UCR | B3 Binding UCR* | B4 FullRefresh UCR | B5 Native UCR* | B6 DCJI UCR |
|---:|---:|---:|---:|---:|---:|
| 0 s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 0.5 s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 1 s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2 s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 5 s | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |

\*B3/B5 denominators include only applicable templates.

## Interpretation

E11 tests a different assumption from E10.

- E10: the defense may choose the wrong predicates because its **transition model is incomplete**.
- E11: the defense chooses the right predicates but receives **temporally stale state values**.

If both B6 and FullRefresh degrade under sufficiently stale telemetry, that demonstrates
that selective reasoning cannot compensate for an observation channel that is itself
out of date. Native atomic commit conditions are expected to be more robust because they
are evaluated against server-side current state.
