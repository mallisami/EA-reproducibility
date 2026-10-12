# FlashIR E13 v3 — Final Pre-Run Live Kubernetes Harness

E13 is frozen **before the first real-cluster scientific output**. It now links the locked DCJI implementation directly to live Kubernetes commit reads instead of hard-coding a “DCJI-style” check.

## Scheduled trials
- Positive mechanisms: F1.1, F1.2, F4.1 and F4.4 — **20 repetitions each** (80 total).
- Live negative controls: NC-L1 stable state and NC-L2 irrelevant mutation — **20 repetitions each** (40 total).
- Conditional F2.1: one Pod-IP-reuse churn attempt per successful cluster session; `NOT_REPRODUCIBLE` is an admissible scientific result.

## Exact run sequence
1. `python3 scripts/preflight_e13.py`
2. If and only if status is `PASS`: `python3 scripts/run_e13_protocol.py --execute`

The wrapper will not create attempt files after a failed preflight. Attempt 2 is automatic only after an `INFRA_FAILURE` on attempt 1. A completed scientific attempt is never rerun merely because the observed mechanism, deadline, guard, comparator or control outcome differs from expectation.

## DCJI integration
`E13_MODEL_BINDINGS.json` binds each case to a byte-locked v8.0 scenario. At the decision point, `dcji_live_adapter.py` verifies the scenario/core hashes and calls the actual `derive_critical_predicates()` planner. Only the returned critical query is mapped to a live Kubernetes read. The observed value is projected into the frozen scenario semantics and evaluated with the actual `B6_dcji_gate()` implementation.

Planning latency is recorded separately because the plan is derived before the induced transition. Commit-gate latency covers the selected live read plus B6 commit evaluation.

## Timing
Every scheduled trial records decision, transition-start, transition-complete, gate-start and gate-end timestamps. It reports transition feasibility under the frozen scenario deadline, guard overhead ratio, and residual actuation budget. On NC-L1/NC-L2, when the gate returns `EXECUTE`, the frozen response is actually issued and `first_actuation_start_time_unix`, `actuation_end_time_unix`, and `gate_to_first_actuation_ms` are recorded. Deadline misses are retained as scientific data.

The gate-to-actuation measurement quantifies the residual non-atomic client-side window; it is **not** interpreted as an atomic safety guarantee.

## Safety boundary
All destructive actions are restricted to namespace `flashir-e13`. F4.4 changes only an EndpointSlice object and induces no packet disruption.
