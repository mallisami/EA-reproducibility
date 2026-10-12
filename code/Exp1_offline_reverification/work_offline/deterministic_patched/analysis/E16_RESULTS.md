# FlashIR E16 — Consequence and Recovery

## Design
E16 evaluates all **23 positive held-out templates** under P1 (minimal) and P4
(high-impact) contexts, using the frozen W2 state and defenses B0, B4, and B6.

This is a deterministic consequence model, not a live outage experiment. Recovery time is
reported in **normalized recovery-time units**, not seconds.

## Severity rubric
- S0: prevented before actuation
- S1: unnecessary but immediately reversible action after justification-only failure
- S2: one benign resource/member disrupted in minimal context
- S3: service-critical disruption/reconciliation risk in P4 tier-1 context
- S4: reserved for explicit persistent/destructive evidence; **not inferred** here

## Results

### OneShot B0
Across 46 P1/P4 positive contexts, B0 executes unsafely in **46/46**.

Severity distribution:
- S1: **6** rows (F5 justification-only cases across P1/P4)
- S2: **20** rows (F1-F4 P1 minimal contexts)
- S3: **20** rows (F1-F4 P4 tier-1 contexts)
- S4: **0** rows

P1 B0:
- mean recovery actions: **2.35**
- mean modeled recovery time: **3.22 units**

P4 B0:
- mean recovery actions: **3.22**
- mean modeled recovery time: **4.96 units**

### FullRefresh B4 and DCJI B6
Both defenses abort/replan before unsafe actuation on all 46 contexts:
- B4: **46/46 S0**
- B6: **46/46 S0**
- recovery actions: **0**
- modeled recovery time: **0**

## Interpretation
The consequence experiment adds an impact dimension to UCR. A single unsafe-commit rate
can hide materially different outcomes: justification-only stale actions are modeled as
reversible S1 consequences, while wrong-target/scope failures escalate from S2 in minimal
contexts to S3 in tier-1 P4 contexts.

No S4 result is manufactured. The frozen benchmark does not explicitly encode durable data
loss or another persistent destructive consequence, so E16 reserves S4 rather than inferring
it from the ambiguous `delete_or_stop_workload` action label.

The recovery ledger is a reproducible normalized model for comparing consequence burden;
it must not be presented as measured production recovery latency.
