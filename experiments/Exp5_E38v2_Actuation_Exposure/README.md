# Experiment 5: E38v2 actuation-time exposure (locked) (RQ3)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Paper: Section 5.5 (method), Figure 2, Section 6.3, Figure 4, Table 6 (results), Supplement S5 (preliminary run: Tables S7a, S7b), S12 (confirmatory rerun: Tables S11-S15b, Figure S2).

| Subfolder | Run | Status in the paper |
|---|---|---|
| `Confirmatory/` | `e38v2r-20260926t1032z`: Running-and-Ready target Pods, 8,728 trials, locked 2026-09-26T10:34:43Z, seed 154080643 | All main-text E38v2 claims (Figure 2, Figure 4, Table 6) |
| `Preliminary/` | `e38v2-20260926t0813z`: scheduling-gated (never started) target Pod, 7,594 trials, locked 08:17:00Z, seed 1772720255 | Supplement S5 only; not cited as a result |

## Question
After a gate accepts, what is the probability that an independent write released δ ms later makes the Pod DELETE take effect in an invalid state? The comparison covers client gates (A0 NativeUID, A1 DCJI, A2 FullRefresh) and server-side admission (A3 = DCJI + validating webhook).

## Design
- Conditions: `no_write`, `async_scope`, `async_app_policy`, `async_evidence_last_literal`, `uid_rebind_control`.
- δ ∈ {−5, −2, −1, 0, 0.5, 1, 2, 3, 5, 10, 20, 50} ms.
- 152 cells. Stage 1 has 39 trials per asynchronous cell plus 39 no_write and 10 UID-control trials per arm (5,812 trials). A pre-registered top-up takes every asynchronous cell with 0.05 < p̂ < 0.95 to 93 trials (confirmatory: +2,916, total 8,728).
- Primary oracle: audit-log resourceVersion commit order, with a pod-watch fallback (plan v1.2).
- Secondary oracle: writer ResponseComplete vs DELETE ResponseComplete. Tertiary: writer ResponseComplete vs DELETE requestReceived.
- Endpoints: p̂ with a Wilson 95% interval, d*, webhook latency cost (HL), false aborts, rule agreement. Estimation only, with no hypothesis tests.

## Folder map (same layout in both subfolders)
| Path | Content |
|---|---|
| `data/raw/*_EXPORT_*.REDACTED.tar.gz` | Complete run export: `RAW_TRIALS.jsonl`, kube-apiserver `audit.log`, `webhook_log.jsonl`, `pod_events.jsonl` (confirmatory), `apiserver.log` (confirmatory), schedules, LOCK, stage-1 input snapshots, ops scripts, `code_snapshot.tgz`, smoke runs. The export was re-packed with minimal redactions (see below). |
| `data/processed/analysis_final/` | Final stage-2 analysis: `cells_E38v2.csv` (source of Table 6, Figure 4, Tables S7a/S11), `exposure_window_E38v2.csv`, `webhook_latency_E38v2.csv`, `availability_E38v2.csv`, `agreement*_E38v2.csv`, `writer_release_E38v2.csv`, `oracle_trials_E38v2.csv` (one row per trial), `results_E38v2.json` |
| `data/processed/analysis_stage1*`, `stage2_topup/` | Stage-1 analyses and the stage-2 top-up decision |
| `data/processed/*.txt, *.json, *.csv` | `A3_MULTI_EVAL_SENSITIVITY.txt` (Tables S7b/S14), `INDEPENDENT_RECOMPUTE.txt`, and for the confirmatory run only: `COMPARISON_FIRST_VS_RERUN.csv` (Table S12, Figure S2), `MECHANISM_APISERVER_SUMMARY.txt` (Table S13), `RUNNING_POD_REPORT.json` and `PROBE_RUNNING_POD*.json` (Tables S15a/b), `E38V2R_TIMING_DESCRIPTIVES.csv` (Figure 2, post hoc) |
| `data/processed/smoke_runs/` | Smoke runs (not results) |
| `protocol/` | `LOCK_E38v2.json`, `ANALYSIS_PLAN_E38v2.txt` (v1.1 preliminary / v1.2 confirmatory), `INTERFACE_SPEC_E38v2.txt`, `RUNBOOK_E38v2.txt` (IP redacted), schedules, rendered deploy manifests |
| `provenance/` | `DEVIATIONS_E38v2*.txt`, operations logs (IP redacted), lock hashes and verifications, `SHA256SUMS` of the export, `SESSION*.json`, `ENVIRONMENT.txt`, `REDACTION_MANIFEST_*.csv`, `ORIGINAL_EXPORT.sha256` |
| `results/` | `HEADLINE_E38v2*.txt`, `HEADLINE_TABLES.txt` |

Code (lock roots): `code/Exp5_E38v2_confirmatory/` and `code/Exp5_E38v2_preliminary/` (runner, writer, pod watch, webhook, deploy manifests, analysis and oracle, tests, protocol, and `ops_scripts/` from the export).

## How to reproduce
- **L1** redraws Figures 2, 4 and S2, rebuilds Table 6 and Tables S7a-S15b from `data/processed`, and checks the class totals (checks H01-H07).
- **L2** (`--steps e38v2p,e38v2r`) verifies the export hash and extracts it. It checks the LOCK against `code` (the only difference is the IP-redacted runbook, documented in `provenance/LOCK_SUBSTITUTIONS.json`). It then re-runs `analyze_e38v2.py` (stage 2 and stage 1), the independent recomputation, the A3 multi-evaluation sensitivity, the running-Pod report, the API-server log mechanism map, the first-vs-rerun comparison, the Figure 2 timing descriptives and the headline tables, and compares every output with `data/processed` and `results/`. Measured time on the experiment VM was about 1 min per run.
- **L3 (live):** `protocol/RUNBOOK_E38v2.txt` and `scripts/L3_LIVE_REEXECUTION.md`.

## Expected outputs (confirmatory)
8,728/8,728 trials, with 0 INFRA_ERROR, 0 UNKNOWN and 0 fail-closed. Classes: 4,979 INVALID_EXECUTED, 2,632 VALID_EXECUTED, 1,077 CORRECT_ABORT, 40 UID_BLOCKED, 0 FALSE_ABORT. The independent recomputation matches 152/152 cells. Client gates: 0.97-1.00 at δ ≤ 2 ms and 0.77-0.87 at 5 ms. The webhook adds HL 30.0 ms [23.2, 33.9]. Rule agreement is 97.8% (κ = 0.977).

## Deviations
Preliminary: D1-D10 (`Preliminary/provenance/DEVIATIONS_E38v2.txt`). Confirmatory: R1-R14 (`Confirmatory/provenance/DEVIATIONS_E38v2R.txt`), including R5, the pod-event deletion-RV fallback added before the lock (used in 55 trials). All are consolidated in `provenance/DEVIATIONS_REGISTER.md`. The governing-evaluation rule for multiple admission calls is an analysis convention (Supplement S12).

## Redactions in this folder
The public IP address of the experiment VM was replaced by `<PUBLIC_IP_REDACTED>` in one line each of `LOG_*_OPERATIONS*.txt` and `RUNBOOK_E38v2.txt`, both in the package folders and inside the export (including `code_snapshot.tgz`). Inside `code_snapshot.tgz`, the pseudonymous expert-reviewer codes in `src/RUNNER_CHANGES.diff` were also replaced by REVIEWERS. Every other export member is byte-identical to the original export. The member hashes are in `provenance/REDACTION_MANIFEST_*.csv`, and L0 verifies them.
