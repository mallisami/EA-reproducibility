# FlashIR E10 — Incomplete Transition-Model Robustness

## Design

E10 deliberately gives the **attacker the complete frozen transition system** while
randomly hiding transitions only from DCJI's planning model.

This avoids the invalid easier experiment where masking would weaken both attacker and
defender.

For each held-out template and each hide probability:

- p = 0.10, 0.20, 0.30, 0.50
- 20 deterministic masks
- mask seed = SHA-256(master seed, template ID, p, replicate)
- each attacker-model transition is independently hidden with probability p
- P1-P4 reuse the same template-level mask and are treated as nested variants
- the actual attacker executes the full-model optimal W2 path
- B6 plans using the incomplete model
- FullRefresh is retained as a model-independent safety reference

## Main results

| Hidden transition probability | Mean template UCR (B6) | 95% template-cluster CI | Templates with ≥1 unsafe mask | Mean B6 query cost |
|---:|---:|---:|---:|---:|
| 10% | 0.191 | [0.148, 0.235] | 22/23 | 0.989 |
| 20% | 0.339 | [0.285, 0.396] | 23/23 | 0.798 |
| 30% | 0.465 | [0.411, 0.524] | 23/23 | 0.596 |
| 50% | 0.693 | [0.626, 0.759] | 23/23 | 0.322 |

## Mechanism check

Across all **1840** positive template-mask cells:

- actual attack path fully visible to B6: **1063**
- B6 unsafe among those: **0**
- actual attack path partially hidden: **777**
- B6 unsafe among those: **777**

This directly tests whether failure is associated with missing modeled transitions,
rather than with arbitrary benchmark instability.

## Safety reference and negative control

- FullRefresh unsafe commits across all E10 rows: **0**
- F5.6 B6 unsafe commits across all masks: **0**
- B6 planning fallbacks: **0** template-mask cells

## Interpretation

E10 measures the degradation of DCJI's modeled guarantee when the transition system is
incomplete. The correct claim boundary is:

> DCJI's safety certificate is conditional on the modeled transition system; missing
> attacker-relevant transitions can cause selective revalidation to omit state reads
> needed for an unmodeled attack path.

The experiment therefore quantifies, rather than hides, the model-completeness
assumption.
