# FlashIR E15 — Concurrent Benign Interference

## Design
E15 uses the prespecified 12-template representative subset frozen before E15 execution:
`F1.2, F1.3, F2.4, F2.6, F3.3, F3.6, F4.3, F4.5, F5.2, F5.4, F5.6, F1.5`.

Three conditions are evaluated:
1. **C0 Benign noise only** — irrelevant concurrent update; action remains valid.
2. **C1 Benign mirror** — a controller/responder/human performs the same state-change
   sequence as the frozen W2 path, but with zero attacker operations and with all mirrored
   transitions excluded from the DCJI attacker model.
3. **C2 Attack + benign noise** — the frozen attacker path proceeds while an independent
   benign event occurs concurrently.

Only C2 is an attacker-positive EA-TOCTOU condition. C1 measures stale-response safety
under benign concurrency and must not be counted as attack prevalence.

## Key results

### C0: benign irrelevant concurrency
Across all 12 templates and B0/B4/B6:
- false aborts: **0**
- action remains valid: **12/12**

### C1: policy-relevant benign concurrency
Among the 11 positive mechanism templates, the mirrored benign sequence makes the queued
action invalid without any attacker operation:
- B0 unsafe commits: **11/11**
- B4 unsafe commits: **0/11**
- B6 unsafe commits: **0/11**

F5.6 remains valid under its benign mirror and causes no false abort.

### C2: attacker path plus benign concurrent noise
Across the 11 attacker-positive templates:
- B0 unsafe commits: **11/11**
- B4 unsafe commits: **0/11**
- B6 unsafe commits: **0/11**

## Interpretation
DCJI's selective commit checks remain effective when independent benign activity occurs
alongside the attacker. Moreover, because its selected queries correspond to material
commit invariants, the same refresh can also catch policy-relevant benign state changes
that would make the queued action stale.

This does **not** mean benign changes are EA-TOCTOU attacks. E15 separates causal attack
classification from commit-safety behavior.
