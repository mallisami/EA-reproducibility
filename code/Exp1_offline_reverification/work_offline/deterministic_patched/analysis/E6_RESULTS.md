# FlashIR E6 — Native Preconditions

## Scope

Primary analysis uses the five held-out F1 templates with frozen applicable native UID
preconditions: **F1.2–F1.6**. F1.1 is retained only as a development sanity check.

The comparison is B0 / B3 / B4 / B5 / B6 across W0–W3, using one P1 representative per
template to avoid pseudo-replication.

## Held-out W2 results

| Defense | Unsafe W2 | UCR | Mean query cost | Control FAR |
|---|---:|---:|---:|---:|
| B0 | 5/5 | 1.000 | 0.000 | 0.000 |
| B3 | 0/5 | 0.000 | 1.000 | 0.000 |
| B4 | 0/5 | 0.000 | 1.000 | 0.000 |
| B5 | 0/5 | 0.000 | 0.000 | 0.000 |
| B6 | 0/5 | 0.000 | 1.000 | 0.000 |

## Mechanism

For every F1.2–F1.6 W2 case, the frozen native UID predicate is false at the real commit
state, so B5 atomically rejects the queued action. The same native predicate remains true
in W0/W1/W3 controls.

B5 therefore has:
- **0/5 unsafe W2 commits**
- **0 false aborts** across 15 valid control worlds
- **0 additional query round trips** in the deterministic cost model

## Coverage boundary

Native preconditions are applicable to only **5/23 (21.7%)**
positive held-out templates in the current benchmark. They cover the F1 identity/binding
cases but do not generalize to F2/F3/F4/F5 justification and scope failures.

Therefore the correct conclusion is:

> Atomic platform-native commit conditions are the preferred defense when the platform
> exposes the exact needed invariant, but they are a narrow complement rather than a
> general substitute for justification-aware commit validation.

Live enforcement semantics remain assigned to E13; E6 is deterministic policy/control
evaluation and does not claim live Kubernetes atomicity has already been demonstrated.
