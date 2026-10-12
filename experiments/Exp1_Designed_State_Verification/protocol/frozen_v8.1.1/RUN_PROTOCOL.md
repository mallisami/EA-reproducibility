# Run Protocol v8.1

## 0. Preserve the v8.0 controlled lock
Do **not** rerun E1–E12, E15 or E16. Validate the existing controlled inputs/results with the supplied manifests and audits only.

## 1. E14 mechanistic add-on
From `deterministic/addons/` run:
1. `python3 run_e14_once.py` — validation only; should report `VALIDATED_NOT_RUN`.
2. When ready to create the scientific add-on output, run exactly once: `python3 run_e14_once.py --execute`.

The script refuses to overwrite an existing E14 result.

## 2. Real Kubernetes E13
From `live_validation/E13/` run:
1. `python3 scripts/validate_e13_static.py`
2. `python3 scripts/preflight_e13.py`
3. if and only if preflight status is `PASS`: `python3 scripts/run_e13_protocol.py --execute`

Preserve raw command logs and the generated attempt result. Attempt 2 is automatic only after an `INFRA_FAILURE` on attempt 1. Unexpected scientific outcomes, deadline misses, null effects or comparator behavior are not rerun. On NC-L1/NC-L2, if DCJI returns `EXECUTE`, perform the frozen response immediately and retain gate-end, first-actuation-start and actuation-end timestamps; this is an instrumentation endpoint, not a new efficacy hypothesis.

## 3. Kubernetes public trace
Download the complete `raw-audit-logs.log` from the frozen dataset source. Normalize with `trace_validation/scripts/normalize_kubernetes_audit.py`. UID-sensitive analysis must use raw audit records.

## 4. LANL HostEvents
Process days 1–90 in order. Each daily `.bz2` file can be normalized and then deleted after its derived JSONL and SHA-256 are recorded. No day selection based on results is allowed.

## 5. Build trace opportunities
Run `extract_trace_opportunities.py` on the normalized files. Preserve parse-error and insufficient-semantics counts.

## 6. Trace replay and replication
Run `run_e17_e20.py`. Report dataset/domain strata and temporal-block results separately.

## 7. Freeze external outputs
After E14, E13 and E17–E20, create a result-only freeze. Do not change mechanism definitions, delay grid, live controls, timing endpoints, exclusions or trace semantics in response to observed results.
