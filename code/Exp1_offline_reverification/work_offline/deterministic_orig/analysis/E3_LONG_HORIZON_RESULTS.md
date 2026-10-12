# FlashIR E3 Prespecified Long-Horizon Completion

## Status
**PASS — the previously omitted prespecified 60 s / 300 s cells are complete.**

Scope:
- F2.2 Public/Ephemeral IP Reallocation
- F4.5 Cloud Tag-Based Scope Expansion
- P1-P4
- Δ = 60 s and 300 s
- 16 raw rows total

The original 576-row primary E3 grid was **not modified**. Its SHA-256 remains:

`45f613804d66ceffd8f8c770fd2c2d30aba02985221031511c4808acb0497561`

## Template-level results

Both templates remain exploitable at 60 s and 300 s.

At both horizons:
- B0 OneShot: unsafe on 2/2 templates
- B2 TTLGuard (2 s): unsafe on 0/2
- B4 FullRefresh: unsafe on 0/2
- B6 DCJI-Gate: unsafe on 0/2

The long-horizon cells therefore confirm expected saturation behavior rather than creating
a new primary effect: once the attacker path is feasible, longer delay does not change
C* or T* for these frozen scenarios; age-based TTL has enough time to trigger refresh.

These rows are supplementary sensitivity cells only and must not be merged into the
six-point primary delay grid for confirmatory inference.
