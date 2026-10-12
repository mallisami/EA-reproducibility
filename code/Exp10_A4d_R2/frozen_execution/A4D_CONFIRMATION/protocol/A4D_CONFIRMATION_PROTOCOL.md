# A4D audit-safe two-host confirmation protocol

**Status:** prospectively frozen for the R2 confirmation rerun after a narrow evidence-validator correction.  
**Purpose:** obtain a complete primary-observation-path confirmation for the direct target-binding arm after the earlier E41 multi-host run suffered audit-log retention loss.  
**Scientific scope:** A4d only. Existing clean E41 A1/A3 data are not repeated and are not altered.

## Research question
On the same class of genuine two-host Azure/K3s deployment, does direct target binding (authoritative target-resident validity state plus UID/resourceVersion DELETE preconditions) retain its protected-boundary behavior when the complete planned audit observation path is preserved end-to-end?

## Deployment
- Two separately provisioned Ubuntu 24.04 Azure Linux VMs on one private VNet.
- K3s `v1.37.0+k3s1`: one server and one worker.
- Target Pod is pinned to the worker (`a4d-role=workload`).
- API audit uses RequestResponse events for test-namespace Pod PATCH/DELETE operations.
- Audit source retention is enlarged to 1024 MiB per file, 20 backups, 30 days.
- A second append-only run-scoped audit stream is captured continuously with `tail -F` from before the preflight probe until after the final trial.
- An online guardian stops the run if the audit stream or Pod-watch process dies, disk space becomes unsafe, or completed trials do not acquire the required audit/watch evidence within the grace interval.

## Arm
Only `A4d_commit_direct` is executed. No A1/A3 data are rerun.

## Invalidating conditions
1. `async_scope`
2. `async_app_policy`
3. `async_evidence_last_literal`

## Fixed delay grid
`delta ∈ {0, 2, 5, 10, 20, 50, 100, 200} ms`.

## Sample size
Main confirmation:
- 3 conditions × 8 delays × 39 repetitions = 936 asynchronous trials;
- 39 `no_write` controls;
- 10 `uid_rebind_control` trials;
- total = **985**.

Smoke confirmation:
- one trial per asynchronous/control cell = **26** trials;
- excluded from scientific analysis.

No adaptive top-up, outcome-triggered stopping, or result-driven schedule extension is allowed.

## Primary classification
The inherited E39/E41 deployment-validated audit/resourceVersion classifier is used. The confirmation is successful as an observation campaign only if the audit-evidence completeness gate passes independently of scientific direction.

For every completed asynchronous A4d trial, the final evidence gate requires:
- the writer Audit-ID has exactly one ResponseComplete event in the run-scoped audit stream;
- successful writer PATCHes carry a responseObject resourceVersion matching the runner-recorded resourceVersion;
- the DELETE Audit-ID has a ResponseComplete event;
- the DELETE Audit-ID always has exactly one ResponseComplete event;
- for a successful DELETE, `responseObject.metadata.resourceVersion` is used when present; when Kubernetes returns 200/202 without that field, the only accepted fallback is the resourceVersion of exactly one `DELETED` Pod-watch event whose UID equals the trial `expected_uid`;
- missing, duplicate, non-numeric, GAP/ERROR, or conflicting fallback evidence makes the run incomplete;
- 409 responses retain the responseStatus/message required for precondition classification.

The Pod-event recorder therefore remains corroborative when the DELETE audit response carries a resourceVersion, and becomes a narrowly scoped classifier fallback only for the documented successful-DELETE responseObject omission. It never substitutes for the DELETE Audit-ID ResponseComplete record itself.

## Engineering stop rules
The scientific outcome never triggers stopping. The online audit guardian may stop only for instrumentation/infrastructure integrity:
- audit stream or Pod-watch process missing;
- required audit/watch evidence absent beyond the evidence grace interval;
- writer responseObject/resourceVersion mismatch;
- critically low disk space;
- inherited runner infrastructure-error rails.

Any such stop is an engineering/data-quality failure. Completed scientific outcomes are retained and reported, but the confirmation is not called complete until a fresh locked run passes the evidence gate.

## Evidence retained
- frozen schedule and lock;
- raw trial JSONL;
- append-only audit stream and normalized deduplicated audit file;
- source audit snapshot at collection;
- online guardian status/events;
- pod-event log;
- webhook log;
- runner/session/stats logs;
- K3s/environment capture;
- analysis outputs;
- audit-evidence completeness report;
- SHA-256 manifest.
