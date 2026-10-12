# Experiment 6: E39 commit-bound enforcement, risk, cost, load and generality (locked) (RQ1-RQ4)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


> **Current-paper mapping (2026-10-02):** The current manuscript uses three reader-facing RQs. E39 provides the primary target-bound enforcement evidence for **RQ2**, with supporting robustness/acquisition evidence spanning RQ1-RQ3. Historical section/table/RQ locators below reflect the earlier four-RQ manuscript and are retained only as run provenance; use `docs/claims_to_evidence.csv` for current locators.

Paper: Section 5.6 (method), Sections 6.1 (E39-G), 6.2 (E39-C), 6.3 (E39-A comparator arms, E39-B, E39-D/E/F), 6.4 (RQ4), Figures 5 and 6, Table 7, Supplement S13 (Tables S16-S21, Figures S3-S6, exploratory D split).
Run: `e39-20260926t1515z`. One LOCK for all sub-experiments, set 2026-09-26T15:22:23Z, schedule seed 972231175. Amendment 1 (δ grid extended to 100 and 200 ms) was made before the lock. 20,734/20,734 planned trials, 0 infrastructure errors.

## Sub-experiments, and where each one is in the data
All sub-experiments share one raw export and one analysis. Rows of `data/processed/analysis_final/cells_E39.csv` carry `experiment` = A...G, and `cell` = `<exp>/<arm>/<variant>/<condition>/<delta>`.

| Sub-exp. | Paper | Arms / factors | Trials | Processed file(s) | Figure / table |
|---|---|---|---|---|---|
| E39-A | 6.3, 6.4 | A1, A3, A4d, A4m × 3 conditions × δ 0-200 ms (+controls); two-stage 39 → 93 | 7,882 | `cells_E39.csv` (experiment A), `exposure_E39.csv` | Fig 5, Table 7, Tables S17, S18 |
| E39-B | 6.3, 6.4 | Poisson λ ∈ {1, 10, 100}/s; A1, A2, A3, A4d; 400 per cell | 4,800 | `B_rates_E39.csv` | Fig 6, Table 7, Table S19 |
| E39-C | 6.2 | Injected latency c ∈ {0, 50, 200, 1,000} ms; sequential/parallel; stable/EA-attack; precomputed/per-incident planning; 60 pairs per cell | 3,840 | `C_latency_E39.csv` | Table 5, Fig S3, Table S20 |
| E39-D | 6.3 | Background load idle / ≈50 / ≈150 req/s; A1, A3; async_scope, 8 δ | 1,872 | `cells_E39.csv` (D), `exposure_E39.csv` (D), `EXPLORATORY_D_SPLIT_BY_CLUSTER_STOP.txt` | Fig S4, Table S21 |
| E39-E | 6.3, 6.4 | NetworkPolicy CREATE; A1, A3 (webhook extended to CREATE); A4 not applicable | 1,248 | `cells_E39.csv` (E) | Fig S5, Table S21 |
| E39-F | 6.3, 6.4 | client-go v0.37.0 vs Python responder; A1, A4d | 936 | `cells_E39.csv` (F) | Fig S6, Table S21 |
| E39-G | 6.1 | Pre-gate cut b, d, f (out of model) or a, c, e (in model); DCJI (A1) vs FullRefresh (A2); 39 each | 156 | `cells_E39.csv` (G) | Table 3, Table S21 |

## Folder map
| Path | Content |
|---|---|
| `data/raw/e39_EXPORT_e39-20260926t1515z.REDACTED.tar.gz` | Complete export (129 MB compressed, about 1.4 GB unpacked): `RAW_TRIALS.jsonl`, `audit.log` (622 MB), `apiserver.log` (680 MB), `pod_events.jsonl`, `webhook_log.jsonl`, `mirror_log.jsonl`, schedules, LOCK copies, stage-1 inputs, probes, ops scripts, `code_snapshot.tgz`, smoke run |
| `data/processed/analysis_final/` | `cells_E39.csv`, `exposure_E39.csv`, `B_rates_E39.csv`, `C_latency_E39.csv`, `oracle_trials_E39.csv` (one row per trial), `results_E39.json`, `independent_E39.json` + `independent_stdout.txt` (independent recomputation), `INDEPENDENT_DIFFERENCES_EXPLAINED.txt` (the 32 differing cells, explained trial by trial), `QUALITY_GATES_E39.json` |
| `data/processed/analysis_A_stage1/`, `stage2/` | E39-A stage-1 analysis and the stage-2 top-up (47 cells) |
| `data/processed/*.json, *.txt` | Probes (`PROBES_E39*.json`), load probe, delay-proxy calibration, mirror probe, exploratory E39-D split |
| `protocol/` | `LOCK_E39.json` (+ stage-1 copy), `E39_DESIGN_SPEC.txt` (with Amendment 1), `ANALYSIS_PLAN_E39.txt` (section 10 items (10), (11), rule 2.3(f)), `E39_ORACLE_INPUT_NOTES.txt`, `E39_RECORD_SCHEMA.json`, `E39_RUNBOOK.txt` (IP redacted), schedules, seed, rendered manifests |
| `provenance/` | `DEVIATIONS_E39.txt` (D1-D6, O1-O5, P1-P2), operations log (IP redacted), lock verification before each sub-experiment (`lock_verify_before_*.txt`), `SHA256SUMS`, sessions, `ENVIRONMENT.txt`, `OTHER_CLUSTERS_STOPPED.txt` (mid-D host change), `host_watch.txt`, driver/runner logs, `GO_BINARY_SHA256.txt`, smoke setup logs, `REDACTION_MANIFEST_e39-20260926t1515z.csv`, `ORIGINAL_EXPORT.sha256` |
| `results/` | `HEADLINE_E39.txt`, `HEADLINE_TABLES_E39.txt` (including the dispatched-DELETE denominators used in Table 7: A1 2,146, A3 2,254, A4d 1,336, A4m 2,146) |

Code (lock root): `code/Exp6_E39/`. This contains `src/` (runner, arms_commit (A4d/A4m), mirror controller, writer, Poisson arrivals, delay proxy, load harness, NetworkPolicy action, Go client source), `webhook/`, `deploy/`, `analysis/` (`oracle_e39.py`, `analyze_e39.py`, `recompute_independent_e39.py`, tests), `protocol/` and `ops_scripts/`.

## How to reproduce
- **L1** redraws Figures 5, 6 and S3-S6, rebuilds Table 7 and Tables S17-S21 from `data/processed/analysis_final`, and runs checks H08-H23.
- **L2** (`--steps e39`) verifies and extracts the export and checks the LOCK. 78 of the 80 locked files are byte-identical; the IP-redacted runbook and the withheld Go binary are documented substitutions. It then re-runs `analyze_e39.py --stage 2 --w-external A2=12.3883385` (as in `final.sh`), `recompute_independent_e39.py`, the quality gates, the headline tables and the exploratory D split, and compares the outputs with `data/processed` and `results/`. Measured on the experiment VM: about 2.5 min for the analysis and independent recomputation, plus 2-9 min for the operational quality-gate script (schema validation of all 20,734 records); 4 GB RAM and 1.5 GB of scratch disk.
- **L3 (live):** `protocol/E39_RUNBOOK.txt`; see `scripts/L3_LIVE_REEXECUTION.md`.

## Expected outputs (paper values)
- A4d: 0/1,287 invalid in E39-A (33 cells, 0/39 each); 0/400 at every λ in E39-B; 0/312 with client-go. 42/1,336 dispatched DELETEs returned 409, all correct aborts caused by the writer.
- A1: window area W_A 13.2-18.6 ms; 30.0, 227.5 and 545.0 invalid per 1,000 at λ = 1, 10 and 100.
- A4m: 1,009/2,087 invalid; mirror lag median 18.3 ms.
- E39-G: DCJI 39/39 invalid vs FullRefresh 0/39.
- E39-C: sequential excess 3.00-3.02·c; break-even c* = 4.0 ms.
- Primary-secondary agreement 98.45% (κ = 0.981). The independent recomputation differs in 32 cells, all explained.

## Deviations and operational events
Pre-lock: D1-D6 (including rule 2.3(f) after the smoke run). Operational: O1-O5, including O5, where the three unrelated kind clusters were stopped at the user's request during E39-D (18:40:42-18:41:17Z). Post-lock observations: P1 (10 A4m UNKNOWN trials) and P2 (the action is dated at final removal). See `provenance/DEVIATIONS_E39.txt` and `provenance/DEVIATIONS_REGISTER.md`.
