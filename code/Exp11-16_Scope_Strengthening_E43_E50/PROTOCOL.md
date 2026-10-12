# Protocol: experiment E43 (grace period)

Fixed before execution. `ea_exp.py lock` hashes this file, the plan, the schedule and the harness; `run` refuses to start or continue if any of them changes. Experiments E46–E50 are specified in `PROTOCOL_ADDENDUM.md`.

## 1. Purpose

E43 asks how evidence-to-action exposure changes when DELETE uses a non-zero grace period. It addresses the zero-grace-only design of the original campaigns.

Two experiments planned earlier are not run because existing data already answer them:
- **Contested direct binding.** The original campaigns contain 226 trials in which a covered write reached the DELETE boundary first (115 kind, 105 two-host, 6 AKS); all were rejected.
- **Terminating-Pod read-back.** Revision accounting on 637 trials of the original timed sweep shows that a PATCH answered by a Pod already marked for deletion created no stored revision.

## 2. Environment

Single-node kind cluster on one Azure VM (4 vCPU, 16 GB), created by `setup.sh`. The harness runs on the VM host. Kubernetes, kind and client versions are recorded in `environment.jsonl`. This is one deployment; results are not between-deployment estimates.

## 3. Mechanisms and manipulation

- **A1, client revalidation.** Gate reads three ConfigMaps (evidence, scope, policy) and the Pod; DELETE carries a UID precondition. The writer PATCHes the scope ConfigMap to `invalid`.
- **A4d, direct target binding.** The same three values are annotations on the target Pod. Gate reads the Pod once; DELETE carries UID and resourceVersion preconditions. The writer PATCHes the scope annotation to `invalid`.
- **Writer release.** δ ms after gate end. δ is a release time, not a commit time.
- **Workload.** `prompt`: pause container, exits on SIGTERM. `ignore-term`: container ignores SIGTERM, so it runs until the grace period expires.
- No retry after a conflict. No adaptive top-up. Cells are randomized within each repetition block from a fixed seed.
- **Execution.** Pods are prepared in batches of 8 and each trial has its own Pod and, for A1, its own three ConfigMaps. The timed section of a trial (gate, DELETE, and any write released within 300 ms) runs strictly one trial at a time, and nothing is created while a batch is being measured. Consecutive timed sections are separated by 0.3 s. Waiting for removal, writes released later than 300 ms, and clean-up run in the background. Pods from earlier trials can therefore be terminating, and a late write from an earlier trial can occur, while a later trial is measured. This differs from the original campaigns, which ran one trial at a time. `run --serial` restores one-at-a-time execution.


## 3.1 Harness-mode serial cross-check

Because E43 uses batched preparation and background teardown whereas the original campaigns executed one trial at a time, the campaign includes a small, separate harness-mode sensitivity check before the full E43 run. `plan_E43_serial_crosscheck.json` uses the same E43 harness with `run --serial` for a representative 5 s `ignore-term` block (A1 and A4d; delays 0, 100, 4500 and 7000 ms; three repetitions plus three no-write controls per arm).

This cross-check is **descriptive and is not pooled with E43**. It does not create an additional hypothesis or a pass/fail criterion. Its purpose is to expose an obvious batching/teardown artifact before interpreting differences from the original one-trial-at-a-time campaigns. The full E43 run remains the pre-specified grace-period experiment regardless of the cross-check outcome; any material discrepancy is reported as a harness-sensitivity boundary.

## 4. Outcomes (computed by `analyze`, from API responses and a per-trial Pod watch)

Ordering uses resourceVersion as a single-store revision. Probe P1 must show strictly increasing revisions across alternating ConfigMap and Pod writes; this is a deployment-validated rule, not a portable Kubernetes contract.

- **Deletion mark:** first Pod event carrying `deletionTimestamp` (the DELETED event if none).
- **Removal:** the DELETED event.
- `pre_mark` (A1): writer revision < mark revision, DELETE accepted.
- `pre_removal` (A1): writer revision < removal revision, DELETE accepted.
- `contested` (A4d): DELETE returned a resourceVersion-precondition 409 and the writer's PATCH committed after the gate read on a Pod not yet marked for deletion.
- `stale_accept` (A4d): the writer's PATCH committed after the gate read on a Pod not yet marked for deletion **and** the DELETE was accepted.
- `postmark_patch` (A4d): PATCH returned HTTP 200 with a response object already carrying `deletionTimestamp`.
- `postmark_persisted`: for such a PATCH, the written value appears in any later Pod event, including the final DELETED object.
- `pre_removal` (A4d): `stale_accept` or `postmark_persisted`.
- `RV_CONFLICT_UNATTRIBUTED`: a resourceVersion 409 with no committed writer change; in a no-write cell this is a false abort.
- `UNKNOWN`: accepted DELETE whose removal was not observed. Excluded from denominators and reported.

## 5. Pre-stated expectations and what would contradict them

- H1: for A1 with `ignore-term`, `pre_removal` stays high for δ up to roughly the grace period and falls to zero for the δ placed after it; `pre_mark` stays confined to a few milliseconds at every grace period.
- H2: for A1 with `prompt`, the removal window is much shorter than the grace period.
- H3: for A4d, `stale_accept` = 0 at every grace period.
- Open question, no prediction: whether A4d `postmark_persisted` is non-zero when grace > 0. If it is, direct binding's effect-level zero does **not** extend to graceful deletion, and only the boundary-level property holds.
- Interpretation limit: under `ignore-term`, a ConfigMap write released before the grace period ends precedes removal by construction. H1 therefore describes how the removal endpoint scales with the grace period; its informative content is the contrast between the mark and removal endpoints.
- H1 is contradicted if A1 `pre_removal` under `ignore-term` falls to near zero within tens of milliseconds, as it does at grace 0.

The grace-0 block also yields, as a by-product, contested trials and post-mark PATCH read-backs that record annotation values, for comparison with the existing evidence.

## 6. Denominators and reporting

Per cell: n minus UNKNOWN, INFRA_ERROR and DELETE_ERROR. Wilson 95% intervals. No pooling across grace periods or workloads. Every cell is reported, including any that contradict Section 5.

## 7. Stop rules and deviations

Run stops only on probe failure, lock violation or cluster loss. An interrupted run resumes from the next unrecorded trial; the resume is logged in `environment.jsonl`. Any other deviation is written to `DEVIATIONS.md` in the run directory before analysis.
