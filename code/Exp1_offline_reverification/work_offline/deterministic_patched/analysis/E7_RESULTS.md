# FlashIR E7 — Justification Redundancy

## Design

E7 isolates the number of **independent sufficient justification bases**:

- |B|=1: controlled construct derived only from development F5.1
- |B|=2: frozen F5.1
- |B|=3: frozen F5.2
- |B|=4: frozen F5.3

No held-out template label was altered.

The frozen primary delay grid `{0,1,2,5,10,30}` is reused as a mechanistic sensitivity,
and each construct is also evaluated at its frozen W2 deadline.

## Independent-basis results at generous 30 s deadline

| Independent bases | C* attacker ops | T* | B6 physical query cost | Critical query count |
|---:|---:|---:|---:|---:|
| 1 | 1 | 0.600 s | 1.000 | 1 |
| 2 | 2 | 1.300 s | 2.000 | 2 |
| 3 | 3 | 1.800 s | 3.000 | 3 |
| 4 | 4 | 2.000 s | 4.000 | 4 |

## First exploitable point on frozen delay grid

| Independent bases | First exploitable Δ | C* | T* |
|---:|---:|---:|---:|
| 1 | 1 s | 1 | 0.6 s |
| 2 | 2 s | 2 | 1.2999999999999998 s |
| 3 | 2 s | 3 | 1.7999999999999998 s |
| 4 | 2 s | 4 | 2.0 s |

## Shared-predicate structural control

At 30 s:

- 3 **independent** bases (F5.2): C* = **3**
- 3 nominal bases with a **shared predicate** (F5.4): C* = **2**

Therefore, simply counting OR branches is not enough. The security-relevant quantity is
the structure of **independent cut requirements**.

## Interpretation

The minimum attacker operation cost increases exactly with independent justification
redundancy in the frozen controlled series:

`1 -> 2 -> 3 -> 4 attacker operations`.

Time-to-invalidation also rises overall, although exact timing reflects the durations
encoded in each frozen construct rather than a universal linear law.

B6 produces **0 unsafe commits** on every exploitable E7 cell.

## Claim boundary

E7 is a controlled mechanism experiment. It supports the claim that independent
justification redundancy raises the minimum attacker effort required for all-basis
invalidation. It does not estimate population-level real-world exploit probability, and
the four constructs are not four independent attack classes.
