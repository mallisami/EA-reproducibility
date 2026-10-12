# Consolidated deviations register (all experiments)

This register summarizes every documented deviation from the pre-registered protocols, runbooks and analysis plans. The authoritative wording is in the per-experiment files linked in each section. "Pre-lock" means decided and implemented before the experiment's LOCK was set.

## Experiment 1: designed-state and offline verification (frozen E5 and the 2026-09-26 re-analysis)
Sources: Supplement S1, S10, S11; manuscript Section 4.3; `experiments/Exp1_Designed_State_Verification/provenance/REANALYSIS_RESULTS_2026-09-26.md`.

| ID | Type | Summary | Impact |
|---|---|---|---|
| X1-a | Code audit (post hoc) | The frozen reference implementation had an unconditional FullRefresh fallback when no selective certificate existed (latent fail-open; not triggered by any frozen scenario). | Patched fail-closed (`code/planner_core/patch.diff`). All frozen results are identical under the patch (regression SUMMARY files). |
| X1-b | Code audit | Candidate subsets were materialized before the search cap applied (memory blow-up at n ≥ 20). | Patched: lazy capped enumeration (Table S9, Figure S1). |
| X1-c | Code audit | A stipulated authorization flag was evaluated instead of a re-read. | Disclosed. The physical experiments perform a SelfSubjectAccessReview. |
| X1-d | Design limitation | The P1-P4 parameterizations are semantically identical, so the effective unit is the 24 specifications. | Counts reported per specification. |
| X1-e | Post hoc | The follow-up 68 W2 specifications were designed after the original result. | Reported separately. |
| X1-f | Post hoc | Brute-force minimality, reachable-state evaluation, out-of-model mutation and the TTL sweep were all added after data collection. | Labelled post hoc (main Limitations; Supplement S9). |

## Experiment 2: physical acquisition cost (E21, E37, E35, E26, E25)
Source: Supplement S1 "Execution facts", `experiments/Exp2_Physical_Acquisition_Cost/data/processed/latency_reanalysis/A0_facts.json`.

| ID | Summary |
|---|---|
| X2-a | E21 was restarted after cell 4 and re-seeded, so cells 5-8 repeat the guard-order sequences of cells 1-4. The restart was not logged. |
| X2-b | E37 used the same seed, so 66.9% of its trial positions share E21's guard order. |
| X2-c | In E21, load is confounded with time: idle cells ran first and were slower. Load effects are not interpreted. |
| X2-d | Harness overhead: up to 430 ms in 13 trials. In parallel mode the timed region includes thread-pool setup. |
| X2-e | The HL estimator, stall exclusion, guard-order and temporal-dependence analyses and the cost model are post hoc. |
| X2-f | An early post-run script bootstrapped gate latency instead of refresh latency. It was corrected before the manuscript; raw data unchanged. |
| X2-g | A transient proxy timeout interrupted trial 1,373. The interrupted cell was discarded and restarted from zero. |
| X2-h | In E25, DCJI was always measured first (position confounded with method). |

## Experiments 3 and 4 (E34/E36, E38 smoke)
| ID | Summary |
|---|---|
| X3-a | In the frozen E34/E36 runners the DCJI and FullRefresh labels execute identical code (`run_e34.py`, lines 237-248). The client-guard DELETE carries no UID precondition, and no post-gate transition is injected (Supplement S4). |
| X4-a | In E38, post-gate edits complete before the DELETE is sent, so acceptance follows from the design (constructive demonstration only). |
| X4-b | Retrospective implementation audit found that the E38 construct-study session declared a 30 s planning horizon while the implementation invoked the planner with a hard-coded 5 s horizon. Horizon-feasibility reanalysis gives the same reported certificate at 5 s and 30 s (also 10 s and 60 s), so the deviation does not change that construct-study result; it is not used for the central RQ1--RQ2 causal claims. |
| X-E13 | In E13 the transition and the gate ran sequentially, not concurrently. The F1 Pods were bound to a nonexistent node. The E29 causal claim was withdrawn (Supplement S2, S3). |

## Experiment 5: E38v2
**Preliminary run `e38v2-20260926t0813z`**: `Exp5.../Preliminary/provenance/DEVIATIONS_E38v2.txt`. All D1-D10 are pre-lock (08:17Z).

| ID | Summary |
|---|---|
| D1 | Dedicated kubeconfig. The shared config of other projects was never modified. |
| D2 | Target Pod python:3.12-slim with a scheduling gate (never started). This is the reason the run is reported as preliminary. Superseded by R1. |
| D3 | Image import via `docker save \| ctr import`, because `kind load` failed. |
| D4 | Extra runner-identity probes (Audit-Id join, RV-vs-timestamp ordering). A no-op PATCH returns the old RV. |
| D5 | Webhook: per-read time stamps, buffered responses + TCP_NODELAY, logging of unevaluated calls. |
| D6 | Oracle lead decisions, and the governing-evaluation rule for multiple webhook evaluations (plan v1.1 (a)-(f)). |
| D7 | Runner live mode; writer in a separate process; stage-2 id offset; runtime constants from the smoke run. |
| D8 | LOCK tool file list corrected; add-stage2 command. |
| D9 | The smoke criterion "rules agree ≥ 95%" was not met (41%), because the v1 secondary rule compared against DELETE receipt. The rules were not changed. Corrected in the rerun (R2). |
| D10 | Negative δ (−5, −2, −1 ms) were realized as release at the start of the final fence read. |

**Confirmatory rerun `e38v2r-20260926t1032z`**: `Exp5.../Confirmatory/provenance/DEVIATIONS_E38v2R.txt`. R1-R12 are pre-lock (10:34:43Z); R13-R14 are post-lock and operational.

| ID | Summary |
|---|---|
| R1 | Genuinely Running-and-Ready target Pod (pause:3.10), readiness wait, per-trial Pod-state recording. |
| R2 | Secondary rule corrected to ResponseComplete vs ResponseComplete (plan v1.2 (g)). The v1 rule is kept as tertiary. |
| R3 | Targeted kube-apiserver logging (`--v=3 --vmodule=store=6,round_trippers=6`) for the multi-evaluation question. |
| R4 | New seed 154080643. Earlier seeds are refused by the tools. |
| R5 | Pod-event recorder, plus a deletion-RV fallback when both pre-registered sources are missing (plan v1.2 (i)). Used in 55 trials. |
| R6 | Wording of plan v1.1 (f) corrected. The rule itself is unchanged. |
| R7-R12 | Exploration cluster recreated; a failed detached setup was redone; a launch-script false negative fixed; runtime constants re-measured; probe Pods use pause; venvs copied (identical packages). |
| R13 | Post-lock: `final.sh` first called the stage-1 analysis without the stage-2 schedule. The gate refused and it was rerun with both schedules. No data or code change. |
| R14 | Post-lock: the operator session paused about 30 min (API rate limit). The detached runner was unaffected. |

## Experiment 6: E39
Source: `Exp6.../provenance/DEVIATIONS_E39.txt`, `OTHER_CLUSTERS_STOPPED.txt`. LOCK 15:22:23Z, seed 972231175. Amendment 1 (δ grid to 200 ms) was made before the lock.

| ID | Type | Summary |
|---|---|---|
| D1 | Pre-lock code | E39-F: split Go-client ops. A4d-go keeps the combined op, with the writer armed at a predicted gate end (limitation recorded per trial). |
| D2 | Pre-lock code | Parser accepts the Kubernetes v1.37 wording of the precondition-failure message (plan section 10 item (10)). Without it, every A4 409 would have been UNKNOWN. |
| D3 | Pre-lock rule | Rule 2.3(f): an A4d writer PATCH on an already-terminating Pod is not committed (17 smoke trials). Smoke agreement went from 93.5% to 100%. |
| D4 | Pre-lock code | The independent recomputation now counts pre-gate cut writes (E39-G) (item (11)). |
| D5 | Pre-lock | Dry-run runtime constants taken from smoke measurements. |
| D6 | Pre-lock | Operator/probe tooling is outside the LOCK and only reads data. |
| O1 | Operational | Order of sub-experiments A, G, E, F, B, D, C, then the A stage-2 top-up. The LOCK was verified before each. |
| O2 | Operational | Image import via ctr (as E38v2 D3). |
| O3 | Operational | Another project's niced `az` process used up to about 1 core for a few minutes during E39-A. |
| O4 | Operational | Probes were run in both the smoke and the run namespace. |
| O5 | Operational / host change | **The three unrelated kind clusters were stopped at the user's request mid-E39-D (18:40:42-18:41:17Z).** E39-D is analysed ITT by scheduled load, with an exploratory before/after split. E39-C and the stage-2 top-up ran without background clusters. |
| P1 | Post-lock observation | Analogous A4m mirror PATCHes on the terminating Pod remain UNKNOWN (10 trials; counted invalid in S1). Rule 2.3(f) was not extended. |
| P2 | Post-lock observation | The action is dated at final removal, so writes between the deletion mark and the removal count as preceding it. |

## Experiment 7: E40 (exploratory)
Source: `Exp7.../provenance/DEVIATIONS_E40.txt`.

| ID | Summary |
|---|---|
| D1 | The approved region and VM size (southeastasia, B2s) were rejected by AKS. The pre-approved fallback koreasouth, Standard_D2as_v5 was used. |
| D2 | A first cluster was deleted before any probe or trial and then recreated. No data came from it. |
| D3 | Deletion of the MC_ node resource group was evidenced indirectly (managed-identity permissions). |
| D4 | No audit log on AKS. Primary = E39 oracle with an empty audit log; secondary = pre-registered client-time rule. |
| D5 | Mechanical code adaptations (prefix rename, in-cluster API rail, memory guards, emptyDir webhook log, server-side apply, pylib ConfigMap, time-boxed block loop, TokenRequest identities). |
| D6 | `analyze_e39.py` unchanged, run with `--design-check off --experiments A`. |
| D7 | The AKS admission enforcer added namespaceSelector expressions. No effect on the run namespace. |
| D8 | A probe's substring check on the v1.36 409 text reported false, but the locked parser handles the text. |
| D9 | The audit-based secondary/tertiary columns are UNKNOWN by construction. |
| D10 | The independent recomputation differs in 10 cells, always with a smaller denominator (no pod-watch fallback). |
| D11 | Exploratory, not pre-registered A4d mechanism table (Table S24). |
| D12 | E39-A results did not exist at E40 analysis time. The kind reference is the E38v2 rerun. |
| D13 | The smoke pod-watch kept running (read-only) during the main run. |

## Experiment 8: E41 two-host mechanism/transportability follow-up
Sources: `experiments/Exp8_E41_Azure_2VM_MultiHost/protocol/E41_PROTOCOL.md` and its README.

| ID | Type | Summary |
|---|---|---|
| E41-O1 | Post-run observation | The primary audit path could not fully resolve 110/936 A4d asynchronous trials because writer responseObjects had rotated out of the retained audit log. The pre-specified Pod-event recorder matched all 110 to the terminating-Pod non-commit signature. This sensitivity does not recreate the missing audit responseObjects and is reported as corroborative, not as replacement primary evidence. |
| E41-D0 | Protocol status | No scientific-outcome-triggered protocol deviation is documented for the frozen E41 main run. The prospectively reduced E39-A factor grid, two-host topology, fixed delay grid, and no-adaptive-top-up rule were specified before execution. |

## Experiment 9: E42 benign target-churn follow-up
Sources: `experiments/Exp9_E42_Benign_Target_Churn/protocol/E42_PROTOCOL.md` and its README.

| ID | Type | Summary |
|---|---|---|
| E42-D0 | Protocol status | No scientific-outcome-triggered protocol deviation is documented for the frozen E42 main run. The fixed benign-churn grid produced 702 analyzed trials; the pooled 81/312 resourceVersion-abort rate is a stress-grid summary, not a production-incidence estimate. |

## Experiment 10: A4d R2 direct-binding confirmation
Source: `Exp10_A4d_Direct_Binding_Confirmation/provenance/POSTRUN_OFFLINE_CORRECTION.md` and the prospective protocol/locks.

| ID | Type | Summary |
|---|---|---|
| A4D-R2-I1 | Prospective instrumentation | Added continuous run-scoped audit streaming, online guardian, final evidence gate and exact-UID DELETED-watch RV fallback. These protect observation completeness and do not change the A4d mechanism or primary scientific classification rule. |
| A4D-R2-P1 | Post-run utility correction | `summarize_a4d.py` initially matched a stale arm literal and produced a zero-trial derived summary. Raw data and `analysis_a4d` were correct; the selector was corrected and the original summary retained. |
| A4D-R2-P2 | Post-run packaging correction | The first return-manifest command included the manifest itself while redirection was creating it. The corrected manifest excludes itself; all scientific payload files are unchanged. |

## Experiments 11--16: E43/E46/E47/E48/E49/E50 scope-strengthening campaign
Sources: `code/Exp11-16_Scope_Strengthening_E43_E50/PROTOCOL.md`, `PROTOCOL_ADDENDUM.md`, and `experiments/Scope_Strengthening_E43_E50_20261003/README_AUTHORITATIVE.md`. The addendum was fixed before execution.

| ID | Type | Summary |
|---|---|---|
| E43-P1 | Pre-specified sensitivity | A separate 30-trial serial harness-mode cross-check was added prospectively to expose batching/teardown sensitivity. It is non-pooled and is not part of the 12,127 primary-trial campaign total. |
| E46-D0 | Protocol status | No post-lock scientific deviation is documented. Predicate-specific ValidatingAdmissionPolicy enforcement and the matched benign-write comparison were run under the pre-execution addendum. |
| E47-A1 | Accounting clarification | E47 contains 1,140 primary trials: 1,080 safety/liveness trials summarized in Table S36 plus 60 no-write safety controls (20 per retry policy). The 60 controls are included in campaign accounting but omitted from Table S36 for compactness. |
| E47-D0 | Protocol status | Retry policies (`none`, `blind`, `revalidate`), maximum five attempts, invalidating-write safety cells, and benign-churn liveness cells were fixed before execution; no scientific-outcome-triggered deviation is documented. |
| E48-D0 | Protocol status | The A6u/A6b quarantine-label PATCH design was fixed before execution. A6b places the gate-read `metadata.resourceVersion` in an `application/merge-patch+json` Pod PATCH; no post-conflict retry is used for this action comparison. |
| E49-D0 | Scope boundary | Five fresh clusters were recreated on the same VM as planned. This is cluster-recreation repeatability, not independent hardware/provider replication. No post-lock scientific deviation is documented. |
| E50-D0 | Scope boundary | The three-control-plane-node topology was executed on one VM as planned. It tests control-plane topology under shared-host resources, not independent-node hardware. No post-lock scientific deviation is documented. |
| E43E50-PK1 | Provenance-only | The original post-run campaign README contained the arithmetic error `12,179`; authoritative evidence totals 12,157 recorded trials (12,127 primary + 30 non-pooled serial). The superseded README is excluded; the correct total is retained in the execution manifest and trial records. |

## Experiment 17: E52 forced-contest replication
Sources: `experiments/Exp17_E52_Forced_Contest_Replication/protocol/`, retained raw archive, and the experiment analysis-reconciliation note.

| ID | Type | Summary |
|---|---|---|
| E52-A1 | Post-run analysis reconciliation | The original Scope-2 diagnostic required writer client-response completion before DELETE dispatch and therefore returned zero contests. For paper-facing integration, E52 is reclassified with the unchanged frozen E39 oracle and retained RequestResponse audit evidence so that the challenge definition matches the primary manuscript. This yields 271 writer-attributed resourceVersion challenges and 0 stale accepts among 9,360 asynchronous trials. No raw observation or frozen E39 classifier was altered. |
| E52-D0 | Protocol status | The E52 small-delay A4d schedule was fixed before execution: 10,144 trials, including 9,360 asynchronous invalidating-write trials plus 624 no-write and 160 UID-rebinding controls. E52 is reported separately and not pooled with the primary 0/42 challenge set. |
| S2-B1 | Scope-2 disposition | E51 and E53 are retained as execution-limited strengthening attempts and are not used to broaden HA or grace-matrix claims. E55 is retained as a read-back diagnostic; 404 responses remain inconclusive and the study is not used as non-commit proof. |

