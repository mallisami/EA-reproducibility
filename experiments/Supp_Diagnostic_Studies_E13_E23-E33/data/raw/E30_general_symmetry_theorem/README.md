# E30 — General Certificate-Size Theorem for Arbitrary-Size Clauses

A deterministic-only extension (no live infrastructure required) that
generalizes E27's Proposition 3 from clauses of exactly 2 literals
(F5's AND-pairs specifically) to clauses of **arbitrary, mixed size**,
with symmetry assigned independently per clause.

## The gap this closes

E27 proved and verified Proposition 3 — certificate size `k+m` for `m`
symmetric clauses out of `k` — but only for the specific case of F5's
structure: `k` clauses, each with exactly 2 literals. A reviewer could
reasonably ask whether this is a genuine general result or an artifact
of F5's particular AND-pair shape.

## The general theorem

**Proposition (general form).** For a policy that is a disjunction of
`k` independent conjunctive clauses, where clause `i` has `s_i` literals,
let `SYM` be the subset of clauses in which the attacker model allows
**any single literal** to be independently invalidated ("symmetric"),
with the remaining clauses having exactly one fixed, designated literal
that is attacker-reachable ("asymmetric"). Then DCJI's minimum
certificate size is exactly:

```
sum_{i not in SYM} 1  +  sum_{i in SYM} s_i
```

— one query per asymmetric clause **regardless of its size**, and all
`s_i` queries for a symmetric clause. E27's Proposition 3 (`k+m` for
uniform size-2 clauses) is the special case `s_i=2` for all `i`. The
underlying argument is identical to E27's (an asymmetric clause's target
literal is fixed and known, so one query suffices; a symmetric clause's
goal set includes every single-literal-false state, so all its literals
must be queried to avoid missing whichever one was actually the attacker's
target) — this experiment establishes that the argument does not depend
on clause size, and checks that the real planner's search behaves as
predicted even when clause sizes are mixed and symmetric clauses are not
contiguous.

## Method

`scripts/verify_general_theorem.py` programmatically generates 8 scenario
variants — never hand-authored — spanning: uniform 2-literal clauses
(reproducing E27's two extremes as a consistency check), mixed 2/3-literal
clauses with only the size-3 clause symmetric, mixed clauses with only
the size-2 clauses symmetric, uniform 3-literal clauses, and three clauses
of three genuinely different sizes (2, 3, 4) with a non-contiguous
symmetric subset. Each is checked against the real, unmodified
`derive_critical_predicates()`. No live cluster is needed, for the same
reason as E27: this checks the planner's logical output, which E21/E25/E26
already establish translates into an identical physical request count.

## Result

**All 8 predictions confirmed exactly** (`results/general_theorem_verification.json`),
including the two non-trivial mixed-size cases that most directly test
generality beyond E27:

| Clause sizes | Symmetric clauses | Predicted | Actual | Match |
|---|---|---|---|---|
| 2,2,2 | none | 3 | 3 | ✓ |
| 2,2,2 | all | 6 | 6 | ✓ |
| 2,3,2 | none | 3 | 3 | ✓ |
| 2,3,2 | only the size-3 clause | 5 | 5 | ✓ |
| 2,3,2 | only the two size-2 clauses | 5 | 5 | ✓ |
| 2,3,2 | all | 7 | 7 | ✓ |
| 3,3,3 | middle clause only | 5 | 5 | ✓ |
| 2,3,4 | 1st and 3rd (non-contiguous) | 7 | 7 | ✓ |

The certificate-size relationship is therefore not an artifact of F5's
specific 2-literal-clause structure — it holds for arbitrary clause sizes
and arbitrary (including non-contiguous) symmetric-clause subsets, with
the same real planner code, unmodified.

## What this experiment does and does not claim

- It is still a claim about disjunctions of independent conjunctive
  clauses (no literal shared across clauses) — policies with shared
  literals or more general Boolean structure are not covered and would
  need a separate argument.
- Like E27, it requires no live infrastructure because it verifies the
  planner's logical output, not a physical acquisition.

## Files

- `scripts/verify_general_theorem.py` — generates all 8 scenario variants
  and checks the prediction; exits non-zero if any prediction fails
  (none did).
- `results/general_theorem_verification.json` — full output.
