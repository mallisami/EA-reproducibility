# FlashIR E3/E4 Held-Out Sensitivity Results

## Protocol

These analyses use the frozen v3.2 settings and the unchanged v2.2.1 engine.
P1-P4 are treated as nested variants; inferential/descriptive rates below are reported
at the **scenario-template level** after verifying identical causal E3/E4 outcomes
across P1-P4.

No solver UNKNOWN/TIMEOUT state occurred.

# E3 — Delay sensitivity

| Delta (s) | Exploitable positive templates | Rate | B0 UCR* | TTL-2s UCR* | B6 UCR* |
|---:|---:|---:|---:|---:|---:|
| 0 | 0/23 | 0.000 | — | — | — |
| 1 | 10/23 | 0.435 | 1.000 | 1.000 | 0.000 |
| 2 | 23/23 | 1.000 | 1.000 | 1.000 | 0.000 |
| 5 | 23/23 | 1.000 | 1.000 | 0.000 | 0.000 |
| 10 | 23/23 | 1.000 | 1.000 | 0.000 | 0.000 |
| 30 | 23/23 | 1.000 | 1.000 | 0.000 | 0.000 |

\*UCR is conditioned on templates that are exploitable at that delay.

### First exploitable delay on the frozen grid

- 1: **10 templates**
- 2: **13 templates**

### E3 interpretation

The delay sweep separates **attack feasibility** from the static W2 construction. It also stress-tests the frozen TTL baseline at the actual commit age. In particular, TTLGuard does not refresh at ages <= 2 s because the frozen rule is `age > TTL`, so any EA-TOCTOU path completing inside that window can bypass TTLGuard while DCJI still performs path-specific revalidation.

# E4 — Attacker capability sensitivity

| Active capability | Exploitable positive templates | Rate | B0 UCR* | B6 UCR* |
|---|---:|---:|---:|---:|
| C0 | 0/23 | 0.000 | — | — |
| C1 | 6/23 | 0.261 | 1.000 | 0.000 |
| C2 | 20/23 | 0.870 | 1.000 | 0.000 |
| C3 | 23/23 | 1.000 | 1.000 | 0.000 |

### Minimum capability required

- C1: **6 templates**
- C2: **14 templates**
- C3: **3 templates**

## Integrity checks

- 576 E3 incident-condition rows.
- 384 E4 incident-condition rows.
- 23 positive held-out templates + F5.6 negative control.
- P1-P4 produced identical causal E3/E4 outcomes within every template/condition.
- F5.6 remained NOT_EXPLOITABLE at every delay and capability.
- No frozen input hash mismatch.
- No solver UNKNOWN/TIMEOUT.
