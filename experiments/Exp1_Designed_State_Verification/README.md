# Experiment 1: Designed-state and offline verification (RQ1)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Paper: Section 5.2 (method), Section 6.1 and Table 3 (results), Supplement S1 (Tables S1, S2), S7 (Table S9, Figure S1), S10 (Table S10), S11.
Internal IDs: E5 (held-out designed worlds), the E5 follow-up W2 set ("pilot68"), E12/E12b/E12c (planner scaling), and the post-hoc offline re-verification of 2026-09-26 (T4 minimality, T5 reachable-state / out-of-model mutation, T6 TTL sweep, T7 scaling).

## Question
Does the DCJI gate decide correctly within its finite model, are its certificates minimal, and how does it behave when the model is wrong (out-of-model mutation) compared with FullRefresh and a TTL heuristic?

## Design and sample sizes
- 24 held-out specifications (23 positive EA-TOCTOU templates plus 1 negative control), each materialized as P1-P4. An audit showed P1-P4 are semantically identical, so the effective unit is the 24 specifications. There are four worlds (W0-W3), giving 384 evaluated world states: 92 invalid and 292 valid.
- 68 follow-up W2 specifications, designed after the original result.
- Post-hoc offline verification with an independent brute-force minimality search over the full query universe (T4), evaluation of every reachable in-model state (T5c), out-of-model mutation of predicates the model treats as immutable (T5d), 73 multi-goal F5 variants (T5b), a TTL sweep with staggered observation ages (T6, 1,150-1,200 worlds per variant), and a planner scaling re-run of the locked vs patched core on the Azure VM (T7).
- The live out-of-model counterpart is E39-G (Experiment 6, `Exp6_E39_Commit_Bound_Enforcement`).

## Folder map
| Path | Content |
|---|---|
| `data/raw/E5_heldout/e5_defense_comparison.json` | Frozen E5 result file (all defenses × templates × worlds), verbatim from the frozen archive |
| `data/raw/E5_followup_W2/pilot_confirmatory_W2.json` | Frozen follow-up (68 W2 specifications) result |
| `data/raw/E12_scaling/*.json` | Frozen E12/E12b/E12c planner-scaling results from the original host (the "frozen record" in Figure S1) |
| `data/processed/frozen_analysis/` | Frozen summaries: `e5_summary.json`, `E1_E5_HELDOUT_RESULTS_CORRECTED.md`, `e12_summary.json` and others |
| `data/processed/offline_reverification/t4_t5/` | T4/T5 outputs. `t4_t5_summary.json` gives the Table 3 counts (for example `t5d.heldout_P1/positive_cost.B6o_unsafe = 91` of `attack_goal = 120`) |
| `data/processed/offline_reverification/t6_ttl/` | TTL sweep. `t6_ttl_sweep_summary.csv` is Table S10 |
| `data/processed/offline_reverification/t7_scaling/` | `t7_scaling.csv` is Table S9 and Figure S1 |
| `data/processed/offline_reverification/regression_*_SUMMARY.txt`, `tests/` | Regression of all frozen deterministic results under the locked and patched core (identical), test logs (23/23 pass) |
| `protocol/frozen_v8.1.1/` | Frozen methodology, statistical plan, run protocol, pre-analysis protocol |
| `provenance/` | Frozen input/result hash manifests, and `REANALYSIS_RESULTS_2026-09-26.md` (the audit and re-analysis report) |

Code: `code/Exp1_deterministic_benchmark_locked/` (frozen benchmark: locked `flashir_core`, scenarios, instances, run and validate scripts, follow-up W2 scenarios and generators), `code/Exp1_offline_reverification/` (T4-T7 scripts and the patched deterministic tree), `code/planner_core/` (locked vs patched core, `patch.diff`, new tests).

## How to reproduce
- `python scripts/L2_rerun_deterministic.py` replays the E5 held-out results, follow-up W2 results, independent T4/T5 checks and TTL sweep from the retained scientific sources.
- **Historical commands:** The authors' commands are in `code/Exp1_offline_reverification/scripts/run_regression.sh` (regression under the locked and patched core), `analysis_t4_t5.py` (T4/T5, independent evaluator `indep.py`), `ttl_sweep.py` (T6) and `scaling_e12.py` (T7). These expect the frozen deterministic tree (`code/Exp1_deterministic_benchmark_locked`) at the paths used on the authors' VM (`~/dcji_reanalysis_20260926/...`, see `setup_reanalysis_vm.sh`), so a reviewer has to adjust paths. Expected runtime is under 30 min on 4 cores, except the uncapped n = 20/24 scaling cells, which need several GB of RAM on the locked core. The portable wrapper now stages these paths automatically for E5, T4/T5 and TTL. Scaling timing reruns remain a separate historical command and are host dependent.

## Expected outputs (paper values)
DCJI and FullRefresh agree on all 384 states: 0/92 unsafe commits and 0/292 false aborts. There are 0/68 discordances on the follow-up. The brute-force search finds 0 cheaper feasible certificates for 24 + 68 specifications and 73 variants. On reachable states both methods have 0 unsafe executions and 0 false aborts. Under out-of-model positive-cost mutation DCJI is unsafe in 91/120 (held-out) and 285/390 (follow-up), while FullRefresh is 0. TTL at ≤ 2 s gives 0 misses; at 5 s, 47-177 misses; at 10 s, 643-798 misses (out of 1,150).

## Deviations and caveats
The E5 expected decisions come from a separately implemented but semantically identical check, so they test implementation consistency and are not independent ground truth. P1-P4 are identical. The follow-up set is post hoc. TTLGuard is degenerate in the frozen scenarios. The code audit found three deviations of the frozen reference implementation from the paper's algorithm (Section 4.3; `provenance/AUDIT_HISTORY.md`); the patched core reproduces every frozen result. See `provenance/DEVIATIONS_REGISTER.md`.
