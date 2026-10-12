# Result integration rules for the manuscript

Do not edit the manuscript until both main experiments have finished and passed engineering/data-quality checks.

## E41

Report:

- deployment facts (two-host K3s, target/webhook on worker, server audit logging);
- complete primary per-cell or compact supplementary cells;
- A1/A3/A4d exposure summaries and direct-binding abort cost;
- primary-versus-ResponseComplete agreement;
- whether the qualitative mechanism pattern is consistent, mixed, or different from E39.

Do **not** claim that exact E39 timing replicated. Do **not** pool E39 and E41 as if trials were exchangeable independent observations unless a new analysis is prospectively justified.

A scientifically unfavorable E41 result is still publishable evidence and may require narrowing the main claim. It must not trigger selective rerunning.

## E42

Report:

- A4d false-RV-abort probability by fixed delay with Wilson intervals;
- A4d no-write baseline;
- valid-execution/other outcome accounting;
- pooled fixed-grid result only as a descriptive stress summary.

Do **not** call the controlled false-abort proportion an operational or production conflict rate.

## Where results should go

- Main paper: one compact paragraph per experiment and, only if necessary, one small combined table/figure. Prefer text if the result is simple.
- Supplement: full E41 cell table, classifier sensitivities, E42 per-delay table, environment/protocol details.
- Reproducibility package: raw logs, schedules, locks, hashes, complete diagnostics.
