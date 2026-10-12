# Protocol addendum: experiments E46–E50

Fixed before execution. `ea_exp2.py lock` hashes this file, `ea_exp2.py`, `ea_exp.py`, `vap.yaml`, the plan and the schedule. Definitions not repeated here are those of `PROTOCOL.md` (environment, writer release, execution in batches, deletion mark, removal, `contested`, `stale_accept`, denominators, Wilson intervals, stop rules).

## Arms added

- **A5v, target-state admission.** Validity state is on the target Pod as in A4d. DELETE carries only a UID precondition. A ValidatingAdmissionPolicy (`vap.yaml`) denies DELETE unless the three validity annotations on the Pod being deleted are `valid`; it passes Pods already in deletion. It reads nothing but the target object.
- **A6b / A6u, quarantine label.** Validity state is on the target Pod. The action is a PATCH that adds a quarantine label, with (A6b) or without (A6u) the gate's resourceVersion in the PATCH body. The effect endpoint is the commit of the label PATCH.
- **Retry policies (A4d).** After a resourceVersion conflict: `none` stops; `blind` re-reads only the version and retries; `revalidate` repeats the full gate and retries only if the action is still valid. At most 5 attempts.
- **Benign writes.** `cond = benign` changes a security-irrelevant annotation on the target Pod. `churn_hz` adds a Poisson stream of such changes during the trial.

## Outcomes added

- `POLICY_DENY`: DELETE rejected by the admission policy.
- `benign_abort`: the action was rejected although no validity-relevant write had committed.
- `REVALIDATE_ABORT`: the revalidating retry found the action no longer valid and stopped.
- `EXHAUSTED`: still conflicting after 5 attempts.
- For A6b/A6u, `stale_accept`: the label PATCH was accepted after the invalidating write had committed.

## Pre-stated expectations and what would contradict them

**E46**
- H6: for A5v, `stale_accept` = 0 among trials in which the invalidating write committed before the deletion mark. This holds only if the API server evaluates the policy against the object it is about to delete. One or more stale acceptances contradict H6 and mean target-state admission is itself check-then-act; the count and Wilson bound are reported either way.
- H7: under benign writes, A5v `benign_abort` = 0 and A4d `benign_abort` > 0 at short delays.
- If the VAP probe fails, E46 is not run and that is reported.

**E47**
- H8 (safety): with an invalidating write before dispatch, `blind` produces `stale_accept` > 0 and `revalidate` produces none, ending in `REVALIDATE_ABORT`.
- Liveness is descriptive: executed fraction, mean attempts and `EXHAUSTED` fraction per churn rate and policy.
- Interpretation limit: H8 for `blind` follows almost directly from the policy's definition. The informative quantities are the liveness costs.

**E48**
- H9: A6b `stale_accept` = 0 with contested PATCHes rejected; A6u `stale_accept` > 0 when the write precedes the action.

**E49 (five freshly created clusters) and E50 (three control-plane nodes)**
- Descriptive. Per-cell proportions are reported per cluster with their range. Five clusters on one VM estimate cluster-to-cluster variation, not hardware or provider variation. E50 runs all nodes on one VM.

## Not pooled

These runs use this harness's classifier and are separate from the original campaigns and from each other.
