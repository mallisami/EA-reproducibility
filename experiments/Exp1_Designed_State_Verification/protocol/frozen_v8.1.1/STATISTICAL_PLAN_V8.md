# Statistical Analysis Plan v8.1.1 Final Methodology Lock

## Locked controlled held-out benchmark
- Unit/cluster: scenario template.
- P1–P4: nested parameterizations.
- Binary paired outcomes: McNemar where applicable.
- Cluster-bootstrap paired risk-difference CI: 10,000 replicates.
- Descriptive proportions: Wilson 95% CI.
- Efficiency: paired query-cost comparison with template-cluster bootstrap; report median/IQR.
- The 5% DCJI-vs-FullRefresh noninferiority criterion remains **not established** by the existing sample.
- Existing controlled inputs/results are not regenerated in v8.1.

## E14 deadline-discrimination stress test
- Classification: mechanistic/descriptive development-scenario analysis, not confirmatory efficacy inference.
- Report planner status, goal count, selected critical-query IDs, normalized query cost, expanded states and subset evaluations for the three frozen horizons.
- Do not attach a new population-level p-value to E14.
- Primary interpretation: whether the finite deadline removes infeasible goal states and reduces the required commit certificate relative to the no-deadline proxy.

## Live Kubernetes E13
- Repeated trials are nested within scenario/control class; do not treat repetitions as independent attack classes.
- Positive cases: F1.1, F1.2, F4.1, F4.4; 20 repetitions each.
- Negative controls: NC-L1 stable state and NC-L2 irrelevant mutation; 20 repetitions each.
- Report separately by case/control: mechanism-observed proportion, DCJI abort/execute proportion, transition-deadline-feasible proportion, gate-within-deadline proportion, and (where applicable) native UID-precondition success.
- False-abort rate is estimated only from NC-L1/NC-L2, with exact/Wilson 95% descriptive intervals.
- Latencies: report median/IQR for planning runtime, transition latency, commit-query latency, full commit-gate latency and decision-to-gate completion.
- Operational metrics: guard-overhead ratio, residual actuation budget, and (for safe execute controls) gate-to-first-actuation latency.
- Gate-to-first-actuation latency is descriptive only; it quantifies the residual client-side non-atomic window and is not treated as proof that no transition can occur in that interval.
- A scientific null/unexpected result is retained as data. Only infrastructure/setup failure may trigger the protocol's one automatic second attempt.
- No pooled attack-class inference across heterogeneous positive mechanisms.

## Public traces
- Main quantity: conditional exposure curve `Pr(T_transition <= Δ | eligible observed lifecycle transition)` for the frozen delay grid.
- Do not call this real-world attack prevalence.
- LANL primary cluster: day; sensitivity cluster: host-day.
- Kubernetes primary cluster: logical resource key.
- Report median/IQR transition completion time and cluster-bootstrap 95% intervals.
- Report all parser failures, missing identifiers, duplicate audit stages and insufficient-semantics exclusions.
- Cross-domain results are stratified; no primary pooled estimate.
