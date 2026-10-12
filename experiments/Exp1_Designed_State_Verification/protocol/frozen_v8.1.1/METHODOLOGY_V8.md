# Methodology v8.1.1 Final Methodology Lock — Literature/Theory Hardened 2026-09-06

## 1. Decision oracle
The upstream response is selected by a deterministic policy/playbook defined before evaluation. This guarantees initial correctness and isolates the post-decision failure rather than conflating it with action-selection accuracy.

## 2. Justification bases
For pending action `a`, enumerate independently sufficient minimal justification bases `B_1...B_k`. Historical evidence is never invalidated merely because it is old; ground truth is the policy value `J(a,S_t)`.

## 3. Attacker-realizable transition system
Transitions have explicit preconditions, effects, duration, capability tier and attacker-operation cost. Observed public-trace transitions establish operational realism only; causal attacker inducibility is established by the capability model and live Kubernetes experiments.

## 4. Deadline-Constrained Justification Interdiction
Search for paths that complete before the queued action's commit deadline and cause all-basis invalidation, target rebinding, or unsafe scope. DCJI enumerates modeled effectful goal states and derives a minimum-cost set of physical queries that makes every such reachable goal fail the commit policy after refresh.

The graph-search and subset/hitting-set primitives are implementation machinery rather than standalone novelty claims.

### 4.1 Frozen algorithmic construction
For a scenario, profile, attacker capability and pending-action deadline `D`, the locked implementation:
1. checks that the initial state is both policy-valid and platform-authorized;
2. enumerates all unique modeled attacker-reachable states obtainable through allowed transitions whose cumulative transition duration is at most `D`;
3. retains as attack goals only states for which platform authorization and execution preconditions remain true while the full commit policy is invalid;
4. identifies dynamic physical queries whose values differ between the initial state and at least one enumerated goal;
5. enumerates all subsets of those candidate queries and orders them by `(total query cost, subset size, lexical query ID)`;
6. for each goal state, constructs the hybrid commit view produced by refreshing only the candidate subset against that goal;
7. returns the first subset for which the deterministic commit policy is invalid in every enumerated goal state.

Traversal continues after an early goal because the attacker may continue changing the world before the actual commit deadline. Query cost is computed over unique query IDs so multiple predicates backed by the same physical read are not double-counted.

### 4.2 Proposition 1 — finite-model coverage soundness
**Statement.** Assume: (A1) the frozen finite transition model contains the relevant attacker transitions; (A2) enumeration completes without `UNKNOWN_TIMEOUT`; (A3) subset search completes without `UNKNOWN_SUBSET_TIMEOUT`; (A4) commit-time refresh returns the true current values of the selected physical queries; (A5) the commit policy and refresh mapping are the frozen deterministic functions; and (A6) the actual commit state is one of the enumerated deadline-feasible attack-goal states. If DCJI returns status `EXPLOITABLE` with query set `Q*`, refreshing `Q*` at that modeled goal state causes the commit policy to reject rather than execute.

**Proof sketch.** `enumerate_attack_goals()` enumerates every unique model state satisfying the frozen attack-goal predicate before the deadline under A1–A3. `derive_critical_predicates()` evaluates each candidate query subset against **every** enumerated goal by refreshing that subset from the initial cached state to the goal state and re-evaluating `commit_components()`. A subset is accepted only when no enumerated goal remains policy-valid after that refresh. Therefore the returned `Q*` rejects every state in the enumerated goal set under A4–A6. This proves modeled goal coverage, not safety outside the enumerated/observed state space.

### 4.3 Proposition 2 — minimum query cost within the enumerated candidate space
**Statement.** Under A1–A5 above and finite candidate-query enumeration, if DCJI returns query set `Q*`, no other enumerated candidate subset with lower total normalized query cost satisfies the same all-goal rejection property.

**Proof sketch.** The implementation enumerates every candidate subset and sorts subsets by total query cost, then subset cardinality and lexical ID only as deterministic tie-breakers. It returns the **first** subset satisfying all-goal rejection. Consequently no lower-cost subset can satisfy that property, because every such subset is evaluated earlier and would have been returned first.

### 4.4 Limits of the propositions
These propositions are intentionally narrow. They do not establish:
- correctness under unmodeled attacker transitions;
- correctness under stale, delayed, corrupted or semantically incomplete telemetry;
- correctness after planner/search timeout, except for the separately implemented fail-safe FullRefresh fallback;
- global minimum cost over observations not exposed in the frozen query model;
- atomicity between client-side refresh and later external actuation;
- real-world attack prevalence.

The locked E10 and E11 robustness experiments empirically expose model-incompleteness and telemetry-lag failure modes rather than hiding them.

## 5. Commit gate and fail-safe behavior
At commit, refresh only the physical queries selected by the frozen DCJI plan, re-evaluate the deterministic policy and target binding/scope, then execute or abort/replan.

**Snapshot/atomicity boundary.** The client-side gate certifies the refreshed snapshot it observes; it does not serialize external state through the subsequent actuation. A relevant transition after the last refresh but before first actuation can therefore create a residual check/use race. Where the exact invariant is exposed by the platform, use a server-side atomic precondition. Otherwise report the gate-to-first-actuation interval and interpret DCJI as a model-relative, observation-relative commit certificate rather than an unconditional transactional safety guarantee.

FullRefresh remains the conservative upper-bound baseline. The locked B6 implementation already fails safe to FullRefresh if selective planning returns an unknown/timeout/no-certificate state. Exact platform-native atomic preconditions are preferred where the platform exposes the needed invariant.

## 6. Evaluation layers
### Layer A — locked controlled mechanism validation
Five mechanism families, 30 templates and 120 incidents. Six development templates were used before the 24-template held-out set. P1–P4 are nested parameterizations, not independent samples. Existing E1–E12/E15/E16 inputs and outputs remain byte-locked and are not rerun in v8.1.

### Layer A2 — E14 deadline-discrimination stress test
E14 is a **mechanistic/descriptive add-on**, not a new confirmatory endpoint. It uses the already-locked development scenario F5.5 to compare a short deadline, the frozen 5 s deadline, and a finite-graph no-deadline proxy. Its purpose is to show whether deadline pruning changes the selected certificate/query cost rather than merely the exploitability label.

### Layer B — live Kubernetes validation
E13 executes 20 repetitions each of F1.1, F1.2, F4.1 and F4.4 plus 20 repetitions each of two negative controls (stable relevant state and irrelevant mutation).

For every positive and control trial, the harness:
1. captures the decision-state observation;
2. verifies the bound frozen scenario/core hashes;
3. invokes the actual locked `derive_critical_predicates()` planner;
4. induces the prespecified state transition (or control condition);
5. performs only the planner-selected live Kubernetes commit read;
6. evaluates the projected live state with the actual locked `B6_dcji_gate()`;
7. records scientific outcome separately from harness execution status.

E13 records decision, transition-start, transition-complete, gate-start and gate-end timestamps, plus transition latency, query latency, full gate latency, deadline feasibility, guard overhead ratio and residual actuation budget. On the two safe live controls, the response is actually actuated after an `EXECUTE` decision and the gate-to-first-actuation interval is recorded descriptively; this does not convert client-side validation into an atomic guarantee. Unexpected scientific outcomes and deadline misses are retained and do not authorize reruns.

### Layer C — public trace grounding
Use the full Kubernetes raw audit trace and all 90 LANL HostEvents days. Extract only prespecified lifecycle mechanisms: Kubernetes same-name UID change, Windows ProcessID reuse after process end, and Windows LogonID reuse after logoff.

## 7. What public traces can and cannot prove
They show that assumed lifecycle transitions occur in real systems. They do not prove malicious intent and do not estimate real-world EA-TOCTOU attack prevalence. Trace replay is explicitly conditional on a counterfactually pending, initially correct response.

## 8. Statistical interpretation boundaries
- P1–P4 are nested parameterizations; template-level clustering remains the appropriate inferential unit where specified.
- The observed equality of DCJI and FullRefresh safety in the held-out W2 sample does **not** establish the prespecified 5% absolute noninferiority margin; manuscript wording must retain that failure to establish noninferiority.
- E14 is descriptive/mechanistic and must not be converted post hoc into a new confirmatory efficacy endpoint.
- Null/adverse E13 or trace results remain reportable scientific outcomes and do not authorize methodology changes or result-driven reruns.

## 9. Claim boundary
DCJI provides a model-relative, observation-relative commit certificate. Transition-model incompleteness or stale telemetry can invalidate a client-side certificate, as demonstrated by the locked robustness experiments. A relevant post-validation/pre-actuation transition is an additional residual race unless the checked invariant is enforced atomically or the environment supplies an independently justified timing/serialization guarantee. Native atomic invariants dominate client-side checks for the exact invariants they expose.

Recent 2026 work on verdict freshness (Approved Too Late/FBS), stateful authorization (Provenact), delayed-action state witnesses (OpenPort), and stale/misbound asynchronous IR evidence (A2A-IRBench) is treated as prior/adjacent art. FlashIR therefore claims neither generic freshness nor generic execution-time revalidation as novel.
