# Experiment 4: E38 integrated smoke (RQ1, RQ3)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Paper: Section 5.4 (method), Section 6.3 "E38 smoke" paragraph, Table 3 (E38 symmetric stress row), Table 4 (last row), Table 5 (E38 certificate computation), Supplement S6 (Tables S8a, S8).

## Question
Does the end-to-end fixture behave as designed? This covers six evidence ConfigMaps, the scope and application-policy ConfigMaps, an epoch fence, a Running-and-Ready Pod and a UID-conditioned DELETE. It also asks what happens to post-gate edits that complete before the DELETE is sent. This is a constructive demonstration, not a rate estimate.

## Design and sample sizes
There are 56 trials: 28 cells (method × condition) with 2 repetitions, on the Azure VM.

## Folder map
| Path | Content |
|---|---|
| `data/raw/e38raw/` | `RAW_TRIALS.jsonl` (56 trials), `SESSION.json`, `SETUP_TRACE.json`, `CELL_COUNTS.csv`, integrity reports, `FILE_CHECKSUMS.txt` |
| `data/processed/` | Re-analysis outputs (2026-09-26): `E38_CELL_TABLE_28.csv` (Table S8), `E38_TRIAL_TABLE_56.csv`, `E38_LATENCY*.csv` (Table 4 last row), `E38_PLANNER_CHECK.json` (≈34 ms certificate computation), `E38_EXPOSURE_WINDOWS_*.csv`, `E38_SYMMETRIC_DETAIL.csv` (Table 3 E38 row), `E38V2_SIZING.*` (sizing input for E38v2), `reanalysis_console.txt` |
| `protocol/` | Trial contract and the original (not executed) E38 draft and lock template |
| `provenance/` | Raw file checksums and integrity report |

Code: `code/Exp4_E38_smoke/src` (runner, model, independent offline oracle, core) and `code/Exp4_E38_smoke/reanalysis` (`e38_reanalysis.py`, `planner_check.py`).

## How to reproduce
L1 rebuilds Table S8 from `data/processed/E38_CELL_TABLE_28.csv` and compares it with the supplement. Re-running `e38_reanalysis.py` on `data/raw/e38raw` is not part of L2, because it expects the authors' VM paths (`~/dcji_reanalysis_20260926/...`). **It was not re-executed during packaging.**

## Excluded
The final cluster snapshot (`e38_cluster_snapshot.tgz`: kind node logs and namespace objects) is not included. It is not used by any table or figure, and its node logs contain third-party e-mail addresses (OS package metadata).

## Caveats
Post-gate edits complete before the DELETE is sent, so acceptance of those DELETEs follows from the design (Section 5.4).
