# E52 analysis reconciliation

E52 was designed as a dedicated forced-contest replication of the E39 A4d direct-binding
mechanism. The schedule was fixed before execution and produced 10,144 trials, including
9,360 asynchronous invalidating-write trials at 0, 0.5, 1, 2, and 3 ms.

The original Scope-2 diagnostic (`original_scope2_timing_diagnostic.py`) defined a contest
by requiring the writer's *client response* to precede DELETE dispatch. That intentionally
conservative timing rule returned zero contests because no writer response completed before
DELETE send. It is retained here as provenance, but it does not match the paper's already
established protected-boundary challenge definition.

For paper-facing integration, E52 is classified with the unchanged frozen E39 oracle from
`03_Code/Exp6_E39/analysis/oracle_e39.py`, using the retained RequestResponse audit log,
Pod-event stream, webhook log, and raw trial records. Under the paper's established
writer-attributed protected-boundary semantics, the reanalysis identifies 271
resourceVersion-precondition CORRECT_ABORT challenges and 0 INVALID_EXECUTED stale accepts
among the 9,360 asynchronous trials. The studies are not pooled; E52 is reported as a
separate replication.

This is an analysis-definition reconciliation, not a change to the E52 observations or to
the frozen E39 oracle. The raw E52 archive is retained unchanged.
