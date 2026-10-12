# E42 protocol — benign target-Pod churn

**Status:** prospectively frozen before main execution.  
**Role in manuscript:** quantify the safety–availability cost of resourceVersion binding under controlled target changes that do not invalidate the security predicate.

## 1. Research question

When target-object resourceVersion binding is used, how often does a deliberately **security-irrelevant target-Pod mutation** cause an otherwise valid DELETE to abort under the fixed timing schedule?

This is a conditional controlled-churn experiment. It does not estimate how often unrelated Pod mutations occur in production.

## 2. Environment

Run on the same frozen two-host K3s environment used for E41, after E41 is complete. Use a fresh run namespace, fresh short-lived ServiceAccount tokens, and a separate locked schedule.

## 3. Arms

- `A1_dcji`: client-side gate and UID-only DELETE. This is the non-resourceVersion control.
- `A4d_commit_direct`: target-resident validity gate and DELETE with UID + resourceVersion preconditions.

The experiment is not a security-exposure comparison. All security-validity predicates are kept true.

## 4. Benign mutation

The controlled writer changes **only** annotation `e38.seq` on the target Pod. This annotation:

- is permitted by the inherited E39 mutation safety rail;
- is not one of the eight target-resident validity annotations;
- does not change evidence, scope, application policy, or UID;
- nevertheless increments the Pod resourceVersion when committed.

After any HTTP 409, the runner re-reads the target and records whether every target-resident security-validity annotation remains true. A resourceVersion 409 is classified as a **false abort** only when that post-action validity check succeeds.

## 5. Fixed delay grid

One benign target update is scheduled relative to gate end at:

`delta ∈ {0, 0.5, 1, 2, 5, 10, 20, 50} ms`.

There is also a `no_write` control for each arm to measure background target resourceVersion churn during the action window.

## 6. Sample size

- 2 arms × 8 benign-churn delays × 39 trials = 624 trials;
- 2 arms × 39 no-write trials = 78 trials.

Main total = **702 trials**.

The separate smoke run uses one block (**18 trials**) and is excluded from the scientific analysis. No adaptive top-up is permitted.

## 7. Primary estimand

For A4d, at each delay:

`P(FALSE_ABORT_RV | scheduled benign target mutation at delta)`

where `FALSE_ABORT_RV` requires:

1. DELETE returns HTTP 409 attributable to a resourceVersion precondition;
2. the target Pod still exists;
3. the post-action read confirms all target-resident security-validity annotations remain true.

Wilson 95% intervals are reported per delay.

## 8. Secondary summaries

- A4d no-write false-abort probability and Wilson interval;
- valid-execution counts for both arms;
- descriptive pooled A4d false-abort proportion across the fixed benign-delay grid (explicitly **not** a production rate);
- whether the scheduled writer response was observed before DELETE response, used only as descriptive timing attribution;
- unexpected 409/error categories and infrastructure errors.

A1 is primarily a control showing action behavior without an RV precondition; it is not interpreted as a security-equivalent alternative to A4d.

## 9. Interpretation rules

- If controlled benign churn produces A4d 409s, report the conditional abort profile as direct evidence of the safety–availability trade-off.
- If no controlled-churn 409s are observed, report zero-event Wilson upper bounds; do **not** conclude that target binding has no availability cost.
- If no-write A4d controls show 409s, report them separately as background target churn rather than attributing them to the scheduled benign writer.
- Every scientifically valid result is retained regardless of direction.

## 10. Engineering quality gates and stopping

- E41 environment/identity/audit gates must already have passed on the same cluster.
- E42 smoke must demonstrate that the benign writer changes `e38.seq` while target-validity annotations remain true.
- No scientific outcome triggers stopping.
- Abort main execution if infrastructure errors exceed 5% after the first 50 newly written records; investigate before any new main run.
- Code/configuration changes after main begins require a new lock and fresh main run; do not pool across changes.

## 11. Evidence retained

Retain frozen schedule/lock, raw E42 JSONL, audit and pod-event logs, environment capture, runner stats, per-cell analysis CSV, summary JSON, and hashes.
