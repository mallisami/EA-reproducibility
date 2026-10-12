# Experiment 3: Running-Pod decisions, E34 and E36 (RQ1)

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Paper: Section 5.4 (method), Section 6.1 (one sentence), Supplement S4 (Table S5).

## Question
Do the gate decisions and deletion effects match the specification on Running-and-Ready Pods, when no post-gate race is injected?

## Design and sample sizes
E34 used one kind node and E36 three kind nodes on the collaborator's laptop. Each had 220 randomized trials with a 5 s horizon. In the frozen runner the DCJI and FullRefresh labels execute identical code (`run_e34.py`, lines 237-248). They are compared with a NativePrecondition arm that sends the decision UID as a DELETE precondition. No post-gate transition is injected.

## Folder map
| Path | Content |
|---|---|
| `data/raw/E34/`, `data/raw/E36/` | Frozen study folders, verbatim: `results/raw_trials.jsonl`, `results/summary.json`, `scripts/run_e34.py` / `run_e36.py`, protocol, scenario, manifests (empty logs folder not included) |
| `protocol/E34/`, `protocol/E36/` | Protocol JSON, protocol hashes, input hashes, README |
| `results/E34_summary.json`, `results/E36_summary.json` | Frozen summaries: 220/220 expected decisions, 0 wrong targets, verified effects, horizon misses |

## How to reproduce
The supplied `results/summary.json` files contain the canonical endpoint values. The frozen runner generates these summaries; current paper evidence locations are identified in the package README. No separate analysis script beyond the runner's summary is needed. A live re-run (L3) needs the frozen runner and a kind v0.32.0 / Kubernetes v1.36.1 cluster. **It was not re-executed during packaging.**

## Expected outputs
E34: 220/220 expected decisions, 0 wrong targets, 120 verified effects, and 1/220 gate-horizon miss (an abort trial). E36: as E34, with 0/220 gate-horizon misses.

## Caveats
The DCJI and FullRefresh labels are identical code in these runners, and no race is exercised, so these studies support decision correctness only. They say nothing about post-gate exposure.
