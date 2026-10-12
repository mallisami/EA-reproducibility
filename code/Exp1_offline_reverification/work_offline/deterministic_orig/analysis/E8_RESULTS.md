# FlashIR E8 — DCJI Ablation Study

## Variants
- **Full DCJI**: deadline-constrained, attacker-realizable, all-basis certificate.
- **NoDeadline**: removes deadline pruning but retains attacker realizability.
- **NoRealizability**: removes the transition model and assumes every dynamic policy query may change adversarially; therefore refreshes all dynamic policy queries.
- **SingleBasis**: keeps deadline and realizability but protects only one currently sufficient justification basis while retaining binding/scope/execution critical queries.

Evaluation uses one P1 representative per held-out template to avoid pseudo-replication.
W2 is the positive EA-TOCTOU world; W0/W1/W3 are valid controls.

## Results

| Variant | W2 UCR | Unsafe W2 | Mean W2 query cost | Control FAR |
|---|---:|---:|---:|---:|
| Full DCJI | 0.000 | 0/23 | 1.261 | 0.000 |
| NoDeadline | 0.000 | 0/23 | 1.261 | 0.000 |
| NoRealizability | 0.000 | 0/23 | 1.913 | 0.000 |
| SingleBasis | 0.130 | 3/23 | 1.000 | 0.000 |

## Query-cost change relative to Full DCJI

| Ablation | Mean Δ cost | Lower / Equal / Higher |
|---|---:|---:|
| NoDeadline | +0.000 | 0 / 23 / 0 |
| NoRealizability | +0.652 | 0 / 15 / 8 |
| SingleBasis | -0.261 | 3 / 20 / 0 |

## SingleBasis
SingleBasis is unsafe on **3/23** positive held-out templates:
`F5.2, F5.3, F5.4`.

## Interpretation
- NoDeadline isolates the efficiency/selectivity value of the deadline.
- NoRealizability quantifies the cost of abandoning cyber-realizable transition analysis and falling back to arbitrary dynamic-policy counterfactuals.
- SingleBasis directly tests why all independently sufficient justification bases must be considered.

NoRealizability is an ablation, not a proposed competing defense. Its all-query refresh is the conservative consequence of removing attacker-realizability information.
