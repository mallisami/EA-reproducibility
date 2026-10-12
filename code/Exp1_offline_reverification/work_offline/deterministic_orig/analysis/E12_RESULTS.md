# FlashIR E12 — Solver Scalability

## Design

E12 uses a synthetic computational stress harness derived **only from development
template F1.1**. The security semantics, one dynamic physical query, and one terminal
EA-TOCTOU goal are held constant. Only the length of an attacker-rooted causal chain is
increased so that the DCJI goal enumerator visits exactly the frozen state targets:

`10, 25, 50, 100, 250, 500`.

There are 30 reported runtime repeats per target. These repeats measure computational
timing variability; they are not independent security incidents.

| Reachable states | Median planning time | IQR | p95 | Expanded | Subset evals | Status |
|---:|---:|---:|---:|---:|---:|---|
| 10 | 0.891 ms | 0.845–0.946 ms | 1.587 ms | 10 | 2 | EXPLOITABLE 30/30 |
| 25 | 1.864 ms | 1.835–1.899 ms | 2.249 ms | 25 | 2 | EXPLOITABLE 30/30 |
| 50 | 4.082 ms | 3.996–4.220 ms | 5.150 ms | 50 | 2 | EXPLOITABLE 30/30 |
| 100 | 11.807 ms | 11.592–12.406 ms | 14.829 ms | 100 | 2 | EXPLOITABLE 30/30 |
| 250 | 87.405 ms | 84.943–91.014 ms | 92.018 ms | 250 | 2 | EXPLOITABLE 30/30 |
| 500 | 572.184 ms | 561.664–619.276 ms | 937.003 ms | 500 | 2 | EXPLOITABLE 30/30 |

## Descriptive scaling

- Median runtime growth, 500 vs 10 states: **642.35×**
- Median runtime growth, 500 vs 100 states: **48.46×**
- Log-log descriptive slope: **1.65** (R²=0.953)

All 180 runs completed below the frozen 100,000-state and 100,000-subset-evaluation
limits.

## Claim boundary

This E12 harness isolates **reachable-state growth with fixed query-set cardinality**.
It does not establish scalability for arbitrary wide branching, large independent
justification bases, or large candidate-query sets. Those dimensions belong to the
redundancy/ablation experiments and should not be inferred from this result.
