# Supplementary diagnostic studies: E13, E23-E33 (Supplement S2, S3)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


These are earlier protocol-frozen follow-up and diagnostic studies. They are reported only in the Supplement (Table S4 and the S3 text). None is an independent replication of the held-out study, and none carries a headline claim. They are included so that the supplementary numbers can be checked.

| Folder | Study | Supplement use |
|---|---|---|
| `data/raw/E13/` | Original live Kubernetes study (protocol, model bindings, scripts `run_e13_once.py`, manifests, `results/e13_live_attempt_1.json`) | S2, Table S4 (horizon accounting), audit corrections |
| `data/raw/E13_live_multi/` | F5.LIVE (six labels on one Pod) | S2 |
| `data/raw/E23_f1_fast_rebinding/` | Same-name rebinding speed (0.023-0.232 s) | S3; Introduction |
| `data/raw/E24_concurrent_fencing/`, `E28_moderate_contention_fencing/`, `E33_fencing_dose_response/` | Coherence under engineered mutation | S3 |
| `data/raw/E27_symmetry_characterization/`, `E30_general_symmetry_theorem/` | Deterministic checks of Proposition 3 | S3 |
| `data/raw/E29_pod_grace_period_diagnosis/` | Deletion grace (causal claim withdrawn) | S3 |
| `data/raw/E32_pod_live_decision_trial/` | Zero-grace decision path | S3 |

Each folder is a verbatim copy of the frozen study folder, with its protocol, scripts, manifests and results (`raw_trials.jsonl`, `summary.json`). Log folders were not copied; the E13 `logs/commands.log` (2.6 MB of shell transcript) is also not included. The original archive paths are in `provenance/FROZEN_EVIDENCE_MAP.csv`. The frozen study summaries provide the canonical endpoint values. Current evidence locations are described in the package README and `docs/claims_to_evidence.csv`. None of these studies was re-executed during packaging.
