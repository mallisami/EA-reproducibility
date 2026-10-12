# FlashIR Pre-Analysis Protocol — v3.2

**Frozen:** 5 September 2026, Asia/Karachi  
**Benchmark:** FlashIR-Bench v3.1  
**Engine:** v2.2.1-auditfix  
**Held-out status at freeze:** unopened for defense comparison

## 1. Primary analysis population

The deterministic held-out benchmark contains **96 incidents from 24 templates**.

- 23 positive EA-TOCTOU templates × 4 parameterizations = **92 positive incidents**
- F5.6 × 4 parameterizations = **4 negative exploitability controls**

P1-P4 are nested variants of the same template and are **not treated as four independent attack mechanisms**.

The primary clustering unit is the **scenario template**.

## 2. Primary endpoint

### Unsafe Commit Rate (UCR)

A trial is an unsafe commit iff:

```text
commit_decision = EXECUTE
AND authorization_valid_at_commit = true
AND actuation_effectful = true
AND valid_at_commit = false
```

The primary UCR denominator contains positive W2 trials for which:

```text
initial_action_correct = true
attack_path_completed = true
oracle attack_goal = true
```

F5.6 is excluded from the positive exploit denominator and reported as a negative control.

## 3. Primary comparisons

1. **B6 vs B0:** security superiority on UCR.
2. **B6 vs B2:** B2 uses the frozen primary TTL of **2.0 s**.
3. **B6 vs B3:** only where a binding predicate exists.
4. **B6 vs B4:** security non-inferiority with absolute margin **5 percentage points**.
5. **B6 vs B4:** query-cost superiority.

B5 is evaluated only where a native atomic precondition is actually defined.

## 4. TTL baseline freeze

Candidate TTLs were prespecified and evaluated using the six **development templates only**:

```text
0.5, 1, 2, 5, 10, 30 seconds
```

Selection rule:

> Choose the largest candidate TTL producing zero unsafe W2 commits and zero false-abort W0/W1/W3 controls on development templates.

The resulting primary value is:

\[
\boxed{TTL = 2\text{ s}}
\]

5 s and above failed all six development W2 cases. The candidate grid remains a sensitivity analysis only.

## 5. Deadline semantics

Primary delay sweep:

```text
Delta = (0, 1, 2, 5, 10, 30) seconds
```

A transition completes before commit exactly when:

```text
cumulative_duration <= Delta
```

Equality counts as completed.

For F2.2 and F4.5 only, 60 s and 300 s are supplementary long-horizon sensitivity points.

## 6. Capability semantics

Primary runs use each scenario's frozen attacker tier.

E4 sweeps:

```text
C0, C1, C2, C3
```

Transitions above the active tier are unavailable. An attacker-induced environment consequence is available only if its attacker-root causal transition is available.

## 7. Statistical inference

### Binary paired defense comparisons

Use paired **McNemar tests** for matched defense outcomes, with a **cluster bootstrap by scenario template** for absolute paired risk-difference confidence intervals.

For descriptive binary rates, report 95% Wilson CIs.

### B6 vs FullRefresh non-inferiority

B6 is considered non-inferior in security if the upper bound of the 95% cluster-bootstrap CI for:

\[
UCR_{B6} - UCR_{B4}
\]

is below:

\[
\boxed{0.05}
\]

### Query cost

For paired B6-vs-B4 query cost:

- median and IQR;
- paired Wilcoxon signed-rank;
- cluster-bootstrap paired-difference CI.

### Multiplicity

Holm correction at alpha = 0.05 is applied to the C1-C3 security-superiority family.

### Delay/capability model

Sensitivity model:

```text
UnsafeCommit ~ log1p(Delta)
             + Defense
             + AttackFamily
             + CapabilityTier
             + (1 | ScenarioTemplate)
```

Report odds ratios and 95% CIs.

## 8. Missingness and solver uncertainty

- `UNKNOWN_TIMEOUT` is never converted to `NOT_EXPLOITABLE`.
- Solver exploitability rates report UNKNOWN separately and include best/worst-case sensitivity bounds.
- B6 trials remain analyzable because its frozen behavior on planning uncertainty is FullRefresh fallback.
- An infrastructure failure receives one exact rerun. A second failure is marked `INFRA_FAILURE`, reported, and excluded from endpoint denominators.
- N/A baselines are excluded only from their own pairwise denominator.

## 9. No adaptive analysis

After the first held-out result is produced:

- no new templates;
- no new P variants;
- no TTL retuning;
- no endpoint changes;
- no baseline-applicability changes;
- no change to the 5% non-inferiority margin;
- no statistical-test switching.

Any implementation correctness bug requires a version bump, documented patch, and full rerun of affected experiments.

## 10. Interpretation constraint from the v3 audit

The benchmark contains **five mechanism families, 30 templates, and 120 incidents**.

It must not be described as 30 independent attack classes. Inference is clustered by template, with family-level interpretation.
