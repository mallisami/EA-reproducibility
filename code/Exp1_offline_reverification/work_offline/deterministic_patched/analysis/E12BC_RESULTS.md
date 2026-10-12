# FlashIR E12b/E12c Exploratory Scalability Extensions

## Status
**PASS — both exploratory stress tests completed without solver timeout.**

These experiments were frozen before execution and derive only from development F1.1.
They do not modify the confirmatory held-out benchmark.

## E12b — branching attacker state space
Binary-tree depths [3, 4, 5, 6, 7, 8, 9] produce exact reachable-state counts
[15, 31, 63, 127, 255, 511, 1023]. Candidate/critical-query cardinality is fixed at 1 and subset evaluations at 2.

Largest case:
- reachable states: **1023**
- attack-goal states: **512**
- median planning runtime: **353.46 ms**
- p95 planning runtime: **363.62 ms**

Descriptive log-log slope of runtime vs reachable states: **1.288** (R²=0.992).
This is an empirical implementation curve, not a theoretical exponent.

## E12c — query/basis certificate combinatorics
The state graph is fixed at 2 states and one attack-goal state. Query cardinality
k ∈ [2, 4, 6, 8, 10, 12, 14, 16]. The construction requires refreshing all k independent sufficient
bases, so the current exhaustive subset search evaluates exactly **2^k** subsets.

Largest case:
- candidate/critical queries: **16**
- subset evaluations: **65,536**
- median runtime: **3173.67 ms**
- p95 runtime: **3425.75 ms**

This exposes a real scalability limitation: the present reference implementation uses
exhaustive subset enumeration, so certificate search becomes combinatorial as independent
candidate-query cardinality grows.

## Interpretation
E12b shows the cost of branching/goal enumeration even when certificate selection is
trivial. E12c isolates the opposite bottleneck: a tiny state graph but exponentially many
query subsets. Together with original E12, these tests establish that FlashIR's current
prototype is practical for small-to-moderate controlled graphs but requires algorithmic
improvements (e.g., direct hitting-set/MILP/branch-and-bound formulations) for large
candidate-query sets.

No claim of linear or polynomial scalability is made.
