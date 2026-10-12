# E38 — Integrated workload / coherent selection / first-effect study

**Status: DESIGN ONLY, NOT RUN.** This document does not alter E21-E37 or constitute prospective registration until a named external operator, independent construct validator, hardware configuration, calibration, pilot results and an immutable signed protocol hash are recorded. E38 MUST NOT be cited as empirical evidence until actual raw measurements exist, pass verification and are analyzed according to this protocol. No result has been manufactured.

## Scientific question and construct

In a single running Kubernetes application, does the *same* executable response pipeline preserve model-relative gate rejection of stale justification, stale target binding and unsafe scope while acquiring fewer physical observations than FullRefresh, and does the response enforce its first-effect invariant? Quantify joint safety, coherence, request cost, latency and deadline feasibility. E21/E37 (ConfigMap-only physical reads), E34/E36 (Running Pod deletion) and E24/E28/E33 (separate coherence stress) do not resolve this joint question.

## Test fixture / topology

- Create a disposable namespace with a Running-and-Ready target Pod, six independently GET-able evidence ConfigMaps (`obs-a` to `obs-f`) and a separately observed scope selector/membership resource. Maintain stable, immutable logical trial IDs and record namespace, resource name, resource UID, resourceVersion and controlled creation/deletion events. Do **not** use a production cluster.
- Bind the queued response to the original Pod UID and choose a narrowly defined action: **delete the bound Pod**, with an explicit UID precondition sent to the Kubernetes API server by *every applicable guard* (including DCJI and FullRefresh). Pod disappearance and deletion request/response are the tested effect; do not rename this "quarantine" or infer full incident remediation.
- Define justification `J=(a AND b) OR (c AND d) OR (e AND f)` over six physical evidence resources. The scope invariant is a separately declared membership/selector predicate, and SameBind compares the current UID with the decision-time UID. Ensure full baseline and optimized guards use identical response-validity semantics; native-UID-only is a **partial-invariant diagnostic control**, not an equally complete safety baseline when other predicates may change.
- Build an explicit transition graph for the selected action: stable, irrelevant change, evidence invalidation, target UID rebinding and scope drift. Separately test overlapping mutation during acquisition. Distinguish attack-capability restrictions from unmodified platform authorization and prove query-to-predicate mappings by actual HTTP GET responses.
- Use a verified coherent observation strategy appropriate to *all required resources*, or abort `UNKNOWN_COHERENCE`; resourceVersion of distinct Kubernetes objects alone is not a cross-object transaction. Do not falsely claim native Kubernetes supplies a global multi-resource snapshot. Log acquisition and fence attempts and independent sequence-numbered mutation history. For any delivered observation vector, independently establish that it coincides with a real state (where history supports such a check).

## Explicit implementation and truth-source prerequisites (P0 gate)

1. Record a signed protocol hash and immutable source commit after independent inspection; store the original planned endpoints, timing rules, scenario generator seed and decision oracle in separate read-only files. No tuning on confirmatory measurements.
2. Create **separate** oracle code or human-adjudicated truth tables from the actual action semantics, not by calling the DCJI decision function to define expected outcomes. Validate UID-binding, current evidence and scope logic on two independently reviewed worked examples for each state transition. Report any adjudication disagreements before trials.
3. Inspect and log the real Kubernetes delete interface and UID preconditions. Verify request payloads, server status codes and read-back Pod UID/absence. Do not count a 409 conflict as a completed delete.
4. For each proposed query, verify independently: physical resource path, request count, decoded predicate, payload bytes, timestamp and version consistency. Confirm that every candidate or mandatory query is charged exactly once. Any query whose semantics cannot be validated is a protocol failure, not an acceptable missing value.
5. Check the planner's returned certificate against exact brute-force enumeration for the fixed model and unit request cost; also check the revised Proposition 3's initial-state/nonempty-goal assumptions where invoked. If the model does not meet the proposition, do not use its formula.
6. Produce a working 2-trial dry-run per condition with zero unsafe operations, valid raw JSON schema, durable logs and complete cleanup. **This is an engineering smoke test, not confirmatory sample.** Resolve all P0 failures before locking the protocol and restarting an *entire* confirmatory campaign.

## Comparison methods / matching

1. **DCJI + server UID precondition:** minimum feasible physical certificate under fixed modeled objective; mandatory authorization and binding queries accounted consistently.
2. **FullRefresh + server UID precondition:** reacquire the same full modeled predicate universe, with the identical coherence treatment and response action.
3. **Native UID-only:** enforce UID precondition without current evidence/scope reacquisition. Report only on UID-binding outcomes and demonstrate its limited applicability for evidence/scope drift; never interpret a failure against an unimplemented invariant as an apples-to-apples DCJI superiority claim.
4. **TTLGuard + server UID precondition:** chosen TTL and stale-cache semantics must be fixed *before* confirmatory tests. Report when it leaves evidence/scope unchecked and whether its applicability is identical across states.

Run methods sequentially in randomized/counterbalanced order within a scenario block after restoring the identical starting state. A block contains matched fresh namespace or reset state; any reset failure is logged, never silently skipped. NativeGuard may be marked NOT_APPLICABLE for unsupported invariants with an explicit denominator; do not score N/A as a safe trial. Run a no-transition negative control in each experimental cell.

## Experimental factors, timing and endpoints

Conditions: stable, irrelevant change, evidence invalidation, UID rebinding, scope drift and mutation-during-acquisition. Cross with idle/moderate background load and sequential/parallel query mode **only where both guards can perform an equivalent policy**. If hardware cannot accommodate every cell, predeclare the supported subset and do not silently pool incompatible conditions.

- Primary safety endpoints: (i) mismatch against independently frozen oracle, (ii) wrong-target deletion verified by before/after UID and server response, (iii) dispatch after abort, (iv) executed deletion without verified effect, each with raw numerator/denominator and exact condition/method attribution.
- Primary efficiency endpoints: physical GET count and total decoded response-body bytes per guard; paired refresh latency in milliseconds (start of acquisition to complete last response), using a monotonic clock.
- Secondary endpoints: gate latency, decision-to-gate horizon miss, decision-to-first-effect horizon miss, coherence delivered fraction, fence retries, abort/replan rate, CPU and memory utilization and observed first-effect time. Record the fixed horizon `D` and first-effect definition before running.
- Log trial ID, cell, block, seed, guard order, all request timestamps, object UIDs and versions, expected decision, observed decision, action dispatched, server status, effect observation, failures and retries. Failed, aborted and timed-out trials MUST remain in the published accounting; neither successful-trial filtering nor imputation is allowed.
- The gate and first-effect horizons are distinct. If a transition itself exceeds the frozen deadline, classify its gate miss without relabeling the no-dispatch abort as an unsafe deletion. Include it in the planned denominator.

## Calibration, size, stopping and analysis

Calibration is independent of confirmatory results: run a physically separate 20-trial-per-method pilot in each planned cell to check endpoint instrumentation, delay ranges, resource cost and within-block variability. This is **not a claim that 20 gives adequate statistical power**. An author and independent statistician must set block count, pairs per block, precision target and fixed total sample size from pilot variance and intended cell-wise uncertainty; enter these numbers and a frozen analysis script in `E38_FINAL_LOCK.json`, which does not yet exist. No optional stopping based on favorable observed effects. Revisions after any confirmatory execution require a new labeled study and transparent explanation; do not overwrite.

For paired latency, report per-cell means/medians, empirical distribution and pair bootstrap intervals conditional on the host; estimate temporal dependence using block-level sensitivity with whole blocks as resampling units only if enough blocks are actually collected. For host generalization, use separately identified independently hosted physical machines and a hierarchical host-level analysis; **one laptop plus three kind nodes is one host**, no matter how many trials it produces. Always include individual latency reversals alongside cell aggregates. No generalized deployment-population claim if host sample is insufficient.

Predeclare a stop-and-investigate infrastructure threshold (for example, more than 5% failed setup attempts within a cell) before lock. Infrastructure failures remain in a complete log. If a P0 truth-oracle, UID-precondition, coherence or instrumentation defect appears, stop the confirmatory campaign, preserve all collected data, repair on a *new version* and restart under a new lock; do not edit the frozen data.

## Handoff and hard release gate

Before launching: verify Docker/kind/Kubernetes versions, physical CPU/RAM, cluster topology, container-image provenance, clock interpretation, API authorization, retention directory and available disk. Pilot all guards, reconcile 100% of smoke requests against GET logs, verify exact observer-to-query mappings, independently adjudicate each oracle case, and checksum input code and protocol. The operator must sign the lock and return source checksums, complete raw trials, full failed-attempt records, request logs, read-back effects, hardware provenance and signed independent assessment. Only after independent raw-to-summary regeneration may an E38 result be inserted in the manuscript.

**Present state: no E38 runner, Kubernetes run, validator sign-off, sample-size calculation, first-effect evidence or external verdict has been completed.** Implementing the runner without validating the actual Kubernetes environment and adjudicated oracles would recreate the repeated-experiment risk identified by the reviewer; this document intentionally does not pretend otherwise.
