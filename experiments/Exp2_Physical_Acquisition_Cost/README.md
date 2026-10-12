# Experiment 2: Physical acquisition cost (RQ2)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Paper: Section 5.3 (method), Section 6.2, Figure 3, Tables 4 and 5 (results), Supplement S1 (Table S3, execution confounds), S3 (E25/E26), S4 (Table S5).
Internal IDs: E21 (local host), E37 (collaborator host, full replication), E35 (collaborator host, 300-pair subset), E26 (second cluster instance), E25 (symmetric attacker). The injected-latency test of the cost model is E39-C (Experiment 6).

## Question
What does selective acquisition save in physical refresh latency and request count, and under which conditions?

## Design and sample sizes
The policy is J = (A∧B)∨(C∧D)∨(E∧F) on six ConfigMaps; DCJI reads 3 objects and FullRefresh reads 6. E21 has 12 cells (load × sequential/parallel acquisition × condition), each 3 blocks × 100 measured pairs, for 3,600 pairs with randomized guard order. E37 has 3,600 pairs, E35 300, E26 300 and E25 90 trials. The protocol endpoint is the paired refresh-latency difference ΔL = L_DCJI − L_FullRefresh (cell mean with a 10,000-resample paired bootstrap). The Hodges-Lehmann shift, stall sensitivity, guard-order strata and the cost model were added post hoc (latency re-analysis of 2026-09-26).

## Folder map
| Path | Content |
|---|---|
| `data/raw/E21/` | Frozen E21 study folder, verbatim: `results/raw_trials.jsonl`, `results/per_request_log.csv`, `results/summary.json`, `results/cell_summaries.json`, `scripts/run_e21.py`, `analyse_e21.py`, `E21_PROTOCOL.json`, README, manifests, scenario (logs folder not included). Also `E21_GUARD_ORDER_SENSITIVITY_V25.csv` |
| `data/raw/E37/`, `data/raw/E35/`, `data/raw/E26/`, `data/raw/E25/` | The other frozen study folders, in the same layout |
| `data/processed/latency_reanalysis/` | Outputs of `code/Exp2_latency_reanalysis/latency_reanalysis.py`: `A1_cell_estimators.csv` (Figure 3, Table 4), `A2_stall_sensitivity_POSTHOC.csv`, `A4*_guard_order*.csv`, `A5*_per_get_latency*.csv`, `A6*_costmodel*.csv` (Table 5), `A7_E25_position_effect.csv`, `A0_facts.json` |
| `protocol/` | Copies of each study's protocol JSON, protocol hash and README |

The original locations in the frozen archive are listed in `provenance/FROZEN_EVIDENCE_MAP.csv`.

## How to reproduce
- **L1** redraws Figure 3 from `A1_cell_estimators.csv` and checks the Table 4 headline values (checks H27-H29).
- **L2** (`--steps latency`) re-runs `latency_reanalysis.py` unmodified on the frozen raw trials and per-request logs. It compares every `A*.csv` and `A0_facts.json` with `data/processed/latency_reanalysis/`. The bootstrap is seeded with 20260926. The authors' VM took about 2-3 min.
- **L3 (live):** the frozen runners (`data/raw/E21/scripts/run_e21.py` etc.) need a kind v0.32.0 / Kubernetes v1.36.1 cluster and the original host setup. Absolute latencies are host-specific (Section 7.5), so a re-run is expected to reproduce the direction and approximate magnitude but not the exact milliseconds.

## Expected outputs (paper values)
Across E21, DCJI issues 3 GETs vs 6, with median refresh latency 29.35 vs 54.40 ms. Cell means range from −11.74 to −128.90 ms (pooled −46.95). HL ranges from −10.9 to −59.5 ms, and the pooled median is −21.49. There are 487/3,600 reversals in E21 and 78/3,600 in E37. E37 sequential HL is −15.4 to −20.3 ms and parallel −8.6 to −11.6 ms. E35 HL is −15.10 [−15.52, −14.72]. E25 HL is +4.89 [−0.60, +10.25].

## Deviations and caveats
The execution confounds are described in Supplement S1: E21 was restarted and re-seeded after cell 4, E37 shared the seed, load is confounded with time, and harness overhead is included. The HL estimator was chosen after data collection. In E25, DCJI was always measured first. See `provenance/DEVIATIONS_REGISTER.md`.
