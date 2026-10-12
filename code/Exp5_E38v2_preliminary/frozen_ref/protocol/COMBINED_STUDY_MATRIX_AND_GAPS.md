# E38 joint campaign — pre-execution study matrix and remaining gaps

**Status:** prospective and not locked. No Azure live trial is included in the package. All study conditions must be settled before confirmatory data collection. This is ONE deployment and ONE orchestrator, with explicitly separated primary, stress, baseline and robustness analyses. Do not pool non-comparable strata or relabel software mocks as empirical trials.

## A. Primary integrated campaign: nine conditions, two comparable methods

Methods: DCJI and FullRefresh, using the *same* server UID-precondition DELETE operation, separately acquired application-policy ConfigMap, actual SelfSubjectAccessReview, and the same controlled epoch fencing. Per guard the predicted stable physical acquisition is DCJI **8 GET +1 SSAR POST** and FullRefresh **11 GET +1 SSAR POST**. These are design counts verified by mocks, not Azure results. The originally supplied E21/E37 comparison was only six evidence GETs versus three, so don't compare percentages without reporting additional mandatory requests.

| ID | Condition | Experimental contribution | Important outcome/limit |
|---|---|---|---|
| 1 | stable | legitimate completion; full-pipeline latency and GET cost | verified Pod disappearance, not full remediation |
| 2 | irrelevant | unnecessary abort under irrelevant changes | action completion should remain possible |
| 3 | evidence_cut | actual three-basis justification invalidation | no DELETE when justification false |
| 4 | uid_rebind | same-name/new-UID transition before gate | stale response rejected |
| 5 | scope_expand | approved singleton becomes larger selector scope | reject expansion before gate |
| 6 | during_acquisition | controller-caused multi-resource read skew | epoch mismatch must produce UNKNOWN and no DELETE |
| 7 | postgate_uid | target replacement after acceptance | expected server HTTP 409 and replacement retained |
| 8 | postgate_evidence | evidence changes after acceptance | UID-only protection **cannot** guarantee safe action; report unfavorable outcome |
| 9 | postgate_scope | scope changes after acceptance | UID-only protection **cannot** enforce separately stored scope |

Smoke: 2 repetitions × 2 methods × 9 conditions = 36 engineering attempts; not confirmatory data. Calibration uses 20/method/cell as a *proposal*, not a final power statement; final N depends on declared precision, temporal block dependence, and approved pilot.

## B. Separate, prespecified extensions on the same deployment

| Condition/stratum | Purpose | Classification |
|---|---|---|
| scope_shrink | scope equality versus permissive subset interpretation | policy semantics stress, not permission-revocation study |
| application_policy_revoke | check named policy ConfigMap read at commit | **NOT** Kubernetes RBAC role revocation |
| unknown_open_epoch | read when controlled mutation is incomplete | unknown/fail-closed check |
| postgate_application_policy | app policy changes after accepted gate | negative post-gate boundary |
| symmetric_bdf_stress | adversary changes b,d,f instead of modeled a,c,e | **OUT OF ASSUMED ATTACK CAPABILITY**; don't pool with primary safety claim |
| TTLGuard | time-based cached evidence/partial scope method | limited comparator; may be unsafe under unmodeled invalidation |
| NativeUID | UID-only atomic target control | partial-invariant comparator; does not evaluate J or scope |
| parallel subset | alternative GET acquisition, excludes during_acquisition | separate predeclared robustness stratum |
| moderate API traffic | unrelated 20 GET/s background request stream | one-host robustness; not production incident workload |

The protocol supports these through `E38_CONDITIONS`, `E38_METHODS`, `E38_MODE` and `E38_LOAD` for separate, clearly labeled smoke campaigns; new confirmatory strata require matching independent review, pilot and immutable lock. Do not add conditions after looking at confirmatory results.

## C. What has *not* been implemented or established

1. **Real Kubernetes RBAC revocation after gate:** app-policy ConfigMap revocation is not RBAC revocation; this additional study is required only if claiming end-to-end safety under real RBAC permission changes. Would require separate disposable ServiceAccount/Role/RoleBinding or equivalent safe instrumentation and validated revocation order.
2. **Atomic cross-resource commit:** controlled even/odd epoch checks work only for participating experimental writers. Arbitrary external writers can bypass the fence and evidence/scope/policy may change after gate. Server UID precondition is insufficient for those predicates.
3. **Independent physical-host replication:** a kind multi-node cluster inside one VM is still one physical host. Optional if presenting broad performance generalization.
4. **Complete incident remediation:** observing Pod disappearance proves the bounded action effect only, not service containment or recovery.
5. **Independently signed E38 oracle:** the original REVIEWER-1 and REVIEWER-2 29-card responses are prior evidence but do not automatically approve these newly designed 14 conditions. A short targeted review by the same reviewers, not another full protocol, is required before claiming prospective external E38 oracle review.
6. **Calibrated sample size and signed lock:** both await actual separate Azure pilot results and author-approved analysis choices.
7. **Real Azure results:** none; this container lacks kubectl, kind, Docker, access to the VM or cloud credentials.

## D. Decision rule for the paper

If live E38 is successful *only within the stated attack capability*, report exact tested domains, gate and use-time outcomes separately, failures/UNKNOWN and negative post-gate outcomes. Never claim full safety against arbitrary post-gate evidence/scope changes. If E38 is not executed, retain component-level manuscript claims and leave E38 unexecuted in the limitation statement.
