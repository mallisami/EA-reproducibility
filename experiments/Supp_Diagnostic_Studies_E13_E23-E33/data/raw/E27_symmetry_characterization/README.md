# E27 — Certificate-Size Characterization Under Partial Threat-Model Symmetry

A deterministic-only extension (no live infrastructure required) that
generalizes E21's restricted-model result and E25's symmetric-model result
into a single proven, computationally-verified relationship, rather than
leaving them as two disconnected data points.

## The gap this closes

E21 shows DCJI needs 3 of 6 observations under a restricted attacker model
(fixed A/C/E). E25 shows DCJI needs 6 of 6 under a fully symmetric model
(either member of every pair). Reported alone, this reads as a fragile,
all-or-nothing result: the 50% saving "disappears" under a different
threat model, with no indication of what happens in between, or why.

## The characterization

**Proposition.** For a policy that is a disjunction of `k` independent
2-literal conjunctive clauses (F5's `(A∧B)∨(C∧D)∨(E∧F)`, `k=3`), let `m`
(`0 ≤ m ≤ k`) be the number of clauses in which the attacker model allows
**either** literal to be independently invalidated ("symmetric" clauses),
and `k−m` the number of clauses in which only one fixed, designated
literal is ever attacker-reachable ("asymmetric" clauses, E21's model).
Then DCJI's minimum certificate size is exactly `k + m`, and the cost
reduction relative to FullRefresh's fixed `2k`-observation certificate is
`(k−m) / (2k)`.

**Why.** For an asymmetric clause, only one literal can ever be the one
an enumerated goal state falsifies, so querying that single literal is
sufficient to detect every such goal — the other literal never needs
checking because it is never the attacker's target. For a symmetric
clause, the goal set includes states where *either* literal is the one
falsified while the other remains true. A certificate that queries only
one literal of such a clause would, in the goal state where the *other*
literal was the one cut, retrieve a value for the queried literal that
matches its cached (true) value and leave the un-queried literal at its
stale cached (also true) value — producing a hybrid view in which the
clause incorrectly evaluates true and the commit policy incorrectly
executes. Both literals of a symmetric clause must therefore be queried
for the certificate to correctly reject every enumerated goal, while
asymmetric clauses contribute only their one reachable literal. Summing
over clauses gives `(k−m)·1 + m·2 = k+m`.

`E21` (`m=0`) and `E25` (`m=3=k`) are the two extreme corollaries,
predicting certificate sizes 3 and 6 respectively — both already
empirically confirmed live. This experiment constructs the two
**intermediate** cases (`m=1`, `m=2`), which had never been tested, and
checks the real, unmodified planner's output against the closed-form
prediction.

## Method

`scripts/verify_symmetry_theorem.py` programmatically generates four F5
variants (`m=0,1,2,3`) — never hand-authored — varying only which
attacker transitions exist per clause, and calls the real, unmodified
`derive_critical_predicates()` on each. No live cluster is needed since
this checks the planner's output, not a physical acquisition; E21, E25,
and E26 already provide the live-infrastructure confirmation for the two
extreme points.

## Result

**All four predictions confirmed exactly** (`results/symmetry_verification.json`):

| m (symmetric clauses) | Predicted certificate size | Actual (real planner) | Predicted reduction | Match |
|---|---|---|---|---|
| 0 (E21's model) | 3 | 3 | 50.0% | ✓ |
| 1 | 4 | 4 | 33.3% | ✓ |
| 2 | 5 | 5 | 16.7% | ✓ |
| 3 (E25's model) | 6 | 6 | 0.0% | ✓ |

The cost reduction is not a fragile, all-or-nothing property of two
disconnected experiments — it is a **linear function of how much
asymmetry the attacker model contains**, `(k−m)/(2k)`, exactly
reproduced by the real planner at every tested point including the two
previously-untested intermediate cases.

## What this experiment does and does not claim

- It is a claim about **this specific policy structure** (a disjunction
  of independent 2-literal conjunctions) — not a fully general theorem
  about arbitrary Boolean policies. Extending it to clauses of size other
  than 2, or to policies with shared/overlapping literals across clauses,
  is future work.
- It does not require live infrastructure because it tests the planner's
  output, which is what actually determines the physical query count in
  E21/E25/E26 — those experiments already establish that the planner's
  logical certificate translates into an identical physical request
  count.
- `m=1` and `m=2` are novel predictions checked here for the first time;
  `m=0` and `m=3` reproduce, at the planner level, results already
  established live in E21 and E25.

## Files

- `scripts/verify_symmetry_theorem.py` — generates all four scenario
  variants and checks the prediction; exits non-zero if any prediction
  fails (none did).
- `results/symmetry_verification.json` — full output.
