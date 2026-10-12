# Why these two experiments and no others

The current manuscript already contains extensive controlled-delay, writer-stress, robustness, client implementation, action-type, managed-control-plane, model-boundary, acquisition-cost, and construct-validation evidence. The remaining reviewer-facing empirical gaps are narrower.

| Remaining reviewer concern | New experiment | Why it has high value | What it does **not** claim |
|---|---|---|---|
| Primary exposure/binding campaigns come mainly from one single-node kind/Azure-VM deployment family | **E41 two-host replication** | Tests the decisive A1/A3/A4d mechanism contrast on a different Kubernetes distribution and two-node deployment while retaining the audit-based evidence chain | Not independent population sampling; not a production incident-rate estimate; exact timings need not reproduce |
| A4d resourceVersion binding can abort on target changes unrelated to security validity | **E42 benign churn** | Directly measures the conditional false-abort cost under controlled non-security target mutation and separates it from a no-write background baseline | Not a natural operational churn-rate estimate |

## Experiments intentionally not added

1. **Exact internal storage-commit instrumentation.** This would require a substantially different systems-instrumentation project and is not necessary for the manuscript's current protected-boundary claim.
2. **Large action catalogue.** The manuscript already tests a CREATE boundary case and explicitly limits the direct-binding result to the stated target-local DELETE design.
3. **Another large same-environment run.** More repeated trials on the original deployment would improve within-campaign precision but would not address the remaining reviewer concern about deployment-level transportability.
4. **Production incident prevalence study.** This is a different estimand and cannot be inferred safely from laboratory writer schedules.
