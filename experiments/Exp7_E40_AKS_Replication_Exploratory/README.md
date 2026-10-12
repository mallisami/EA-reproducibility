# Experiment 7: E40 managed-control-plane replication on AKS (EXPLORATORY) (RQ3, RQ4)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


> **Current-paper mapping (2026-10-02):** This exploratory AKS study is a transportability check for current **RQ1-RQ2**. Historical section/table/RQ locators below reflect an earlier manuscript organization; use `docs/claims_to_evidence.csv` and current Supplement S3 for authoritative locators.

Paper: Section 5.7 (method), paragraphs in 6.3 and 6.4, Supplement S14 (Tables S22-S24, Figure S7).
Run: `a20260926`. One free-tier AKS cluster with one Standard_D2as_v5 node, Kubernetes v1.36.3, region koreasouth (pre-approved fallback). Created for the run and deleted afterwards. 2,019 trials, schedule seed 40926017, locked before the first trial (`protocol/LOCK_E40.json`).

**Status: exploratory support, not confirmation.** AKS exposes no API-server audit log, so the primary oracle is the unchanged E39 oracle without audit input (client-recorded response resourceVersions, with the pod-watch deletion event as fallback). A pre-registered client-timestamp rule replaces the audit-based secondary rule. The A4d mechanism classification (Table S24) was added after the data were seen.

## Design
E39-A replication for A1, A3 and A4d. Conditions `async_scope` and `async_evidence_last_literal`, δ ∈ {0, 2, 5, 10, 20, 50, 100, 200} ms, 39 trials per cell plus controls. The runner, writer, webhook and target Pod ran inside the cluster on its single node. The locked E39 code was copied with mechanical adaptations (`protocol/RENAME_E39_TO_E40.diff`, `protocol/COPY_SHA256_E39.txt`).

## Folder map
| Path | Content |
|---|---|
| `data/raw/E40_EXPORT_a20260926.REDACTED.tar.gz` | Export: `results/a20260926/` (`RAW_TRIALS.jsonl`, `pod_events.jsonl`, `webhook_log.jsonl`, empty `audit.log`, schedule, LOCK, analysis, supplement), smoke run, probes, protocol, code copies (E40 and locked E39), render, k8s and ops files. Re-packed: the Azure identifiers and cluster FQDN in `ops/aks_show.json` and `ops/aks_create.json` were redacted, and the vendored third-party wheel bundle `pylib.tgz` was dropped (its hash stays in the LOCK). |
| `data/processed/analysis/` | Output of `analyze_e39.py` on the E40 run: `cells_E39.csv` (Table S22, Figure S7; named `cells_E40.csv` in the manuscript builder), `exposure_E39.csv`, `oracle_trials_E39.csv`, `results_E39.json`, `independent_E40.json` |
| `data/processed/supplement/` | `client_secondary_E40.csv` and `client_secondary_cells_E40.csv` (secondary rule), `comparison_kind_vs_aks_E40.csv` (Table S23), `SUPPLEMENT_E40.json` (agreement 89.9%, κ = 0.83; webhook HL +102.4 ms; A4d 409 6/624), `A4D_MECHANISM_EXPLORATORY.json` |
| `data/processed/*` | Quality gates, run stats, stdout logs, `PROBES_E40.json`, smoke run |
| `protocol/` | `E40_PROTOCOL.txt` (written before the cluster existed), `LOCK_E40.json`, `LOCK_E39_copy.json`, `COPY_SHA256_E39.txt`, `RENAME_E39_TO_E40.diff`, `REMAINING_E39_TOKENS.txt`, `SCHEDULE.json` |
| `provenance/` | `DEVIATIONS_E40.txt` (D1-D13), `LOG_E40_OPERATIONS.txt`, `INCLUSTER_SHA256.txt`, `SHA256SUMS_E40.txt`, session, runner log, `REDACTION_MANIFEST_E40_a20260926.csv`, `ORIGINAL_EXPORT.sha256` |
| `results/` | `HEADLINE_E40.txt`, `A4D_MECHANISM_EXPLORATORY.json` (Table S24) |

Code (lock root): `code/Exp7_E40/`. This contains `code/` (adapted E39 tree), `code_e39_locked/` (verbatim copy of the locked E39 files), `e40/` (in-cluster main, pod watch, supplement, mechanism, lock and headline scripts), `k8s/`, `render/`, `protocol/` and `ops/` (Azure identifiers redacted).

## How to reproduce
- **L1** redraws Figure S7, rebuilds Tables S22-S24, and runs checks H24-H26.
- **L2** (`--steps e40`) verifies and extracts the export and checks `LOCK_E40.json`: 64 of 65 locked files are identical, and `pylib.tgz` is withheld as documented. It then re-runs `analyze_e39.py` with the E40 options (`--stage 1 --experiments A --design-check off`), the independent recomputation, `e40_supplement.py` (with the E38v2 confirmatory cells as the kind reference, as at analysis time; deviation D12) and `a4d_mechanism.py`, and compares all outputs. Measured time: about 20 s.
- **L3 (live)** needs an Azure subscription and AKS. It is costly and account-specific; see `code/Exp7_E40/e40_ops.sh` and `protocol/E40_PROTOCOL.txt`. **It was not re-executed during packaging.**

## Expected outputs
A1: 0.64-0.69 at 5 ms, 0.05-0.08 at 10 ms, 0/39 from 20 ms, area 6.3-6.8 ms. The webhook adds HL +102.4 ms [100.7, 104.2]. A3 scope write: 0/39 at 0-20 ms, 35/39 at 50 ms, 21/39 at 100 ms. A4d: 409 in 6/624. The primary client-RV rule classifies 102 A4d trials at δ ≤ 5 ms as invalid; all 102 writes came after the deletion was accepted (exploratory Table S24).
