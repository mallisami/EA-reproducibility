# Experiment 10: A4d direct-binding audit-complete confirmation (current RQ2 mapping)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


> **Current-paper mapping (2026-10-09):** This completed study is integrated into current **RQ2** and Supplement **S17/Tables S31-S32**. Earlier October-1 notes describing it as evidence for a future manuscript revision are historical provenance and are superseded by the updated Discover Computing paper identified in the package README.

**Run:** `a4dcr-r2-20261001` on 2026-10-01. **Status:** completed locked confirmation, integrated into the current synchronized manuscript. The experiment remains a separately replayable evidence layer in this package.

## Purpose
The earlier A4d follow-up had an audit-retention defect: the scientific trials completed, but the primary audit observation path did not retain all required ResponseComplete evidence. This run therefore repeated **only A4d direct target binding** with stronger observation retention. The A4d mechanism and scientific outcome criterion were unchanged. The instrumentation additions were prospective: a run-scoped append-only audit stream, online evidence guardian, final all-trial evidence gate, and a narrowly defined UID-matched Pod-watch fallback when a successful DELETE audit response omitted `responseObject.metadata.resourceVersion`.

## Frozen design
- Arm: `A4d_commit_direct` only.
- Asynchronous invalidations: `async_scope`, `async_app_policy`, `async_evidence_last_literal`.
- Delay grid: 0, 2, 5, 10, 20, 50, 100, 200 ms.
- 39 repetitions per condition-delay cell: **936 asynchronous trials**.
- Controls: **39 no-write + 10 UID-rebind = 49 controls**.
- Main schedule: **985 trials**. Smoke: 26 trials, excluded from main analysis.
- No adaptive top-up and no outcome-triggered stopping.
- Schedule seed: 100120261; main schedule SHA-256 `c9ea03d64d6604540868beb64013f9b096e47d6b70f39d152df41e7b74fe2fb4`.

## Environment
Two-host K3s v1.37.0+k3s1 on Azure. The server and worker were distinct Ready hosts; the target was pinned to the worker by `a4d-role=workload`. Sanitized Azure metadata in `provenance/environment/` records region, VM size, image and zone without subscription/tenant/resource IDs.

## Results
| Endpoint | Result |
|---|---:|
| Main trials completed | **985/985** |
| Infrastructure errors | **0** |
| Asynchronous invalid executions | **0/936** |
| Pooled Wilson 95% upper bound for invalid execution | **0.4087%** |
| No-write false aborts | **0/39** |
| UID-rebind controls blocked by precondition | **10/10** |
| Primary-secondary exact agreement | **985/985 (100%)** |
| Critical audit records validated | **1921/1921** |
| Evidence-gate errors | **0** |
| Successful DELETE RV from audit response | **958** |
| Successful DELETE RV from UID-matched `DELETED` watch fallback | **17** |

Each of the 24 asynchronous condition-delay cells had 0/39 invalid executions. Among asynchronous writer attempts, 112 PATCHes returned HTTP 200 and 824 returned 404 because deletion had already progressed; all 112 successful writer responses carried a Pod `deletionTimestamp` in their matched audit response. This diagnostic is in `results/WRITER_OUTCOME_SUMMARY.json`.

The result supports the measured claim that direct UID/resourceVersion binding prevented stale target deletion from being classified as an invalid execution over this frozen grid. It does **not** establish a universal zero probability outside the measured conditions.

## Evidence-completeness gate
`results/AUDIT_EVIDENCE_COMPLETENESS.json` is independent of outcome direction. A successful DELETE always requires exactly one matching ResponseComplete audit record. If that response object lacks a resourceVersion, the only permitted fallback is exactly one `DELETED` Pod-watch event for the same UID with a numeric resourceVersion. Missing, duplicated, malformed or conflicting evidence fails the gate. The completed run passed with 1921/1921 critical records validated and 17 sanctioned watch fallbacks.

## Post-run utility correction
The completed run's raw evidence and locked analysis were not changed. A derived summary utility initially matched a stale literal arm name and produced `totals.n = 0`; the canonical corrected summary now reports 985 trials. The return-bundle script also originally enumerated its own manifest while shell redirection was creating it. Both utility defects are documented in `provenance/POSTRUN_OFFLINE_CORRECTION.md`; the original erroneous summary and manifest are retained. The exact prospective R2 source is under `code/Exp10_A4d_R2/frozen_execution/`, while the two post-run maintenance fixes are kept separately in `code/Exp10_A4d_R2/postrun_maintenance/`.

## Folder map
| Path | Content |
|---|---|
| `data/raw/` | `RAW_TRIALS.jsonl`, normalized and streamed audit evidence, Pod-watch events, webhook log, K3s journal, runner/guardian/watch logs and collection facts |
| `data/processed/analysis_a4d/` | Exact E39 analyzer outputs for the 985 A4d trials |
| `protocol/` | Prospective protocol, main/smoke locks and byte-frozen schedules |
| `results/` | Corrected confirmation summary, evidence-completeness gate, preflight, run stats, session, headline and writer diagnostic |
| `provenance/` | Environment, rendered manifests, original buggy derived artifacts, correction note, exact source archives and their hashes |
| `code/Exp10_A4d_R2/` | Exact R2 execution source plus separate post-run maintenance utilities |

## Offline reproduction
Run `python scripts/L2_verify_a4d_r2.py`. It re-runs the locked E39 analyzer from the packaged raw logs, re-runs the final audit-evidence gate, regenerates the corrected A4d summary, and compares the scientific outputs with the packaged results. `results_E39.json` is compared after removing only path/time provenance keys (`generated_utc`, `inputs_sha256`) because absolute source paths differ across machines.
