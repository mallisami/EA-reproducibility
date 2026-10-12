# E34 — Independent-host running-workload end-to-end validation

E34 addresses the strongest remaining live-execution limitation in V26: the prior diagnostic Pod was intentionally unscheduled. E34 uses a real scheduled nginx Pod that must be `Running` and `Ready` before each decision.

## Conditions

- `stable`: no safety-relevant change; expected `EXECUTE`.
- `irrelevant_mutation`: irrelevant label changes; expected `EXECUTE`.
- `binding_rebind`: running Pod is zero-grace deleted and recreated under the same name/new UID; expected `ABORT_REPLAN`.
- `scope_drift`: same Pod remains but `response-allowed=false`; expected `ABORT_REPLAN`.

DCJI and FullRefresh are evaluated on all four conditions. A native Kubernetes UID delete precondition is evaluated for stable, irrelevant mutation, and binding rebinding; it is intentionally not treated as sufficient for scope drift because UID equality does not encode that predicate.

An accepting client-side gate sends a real zero-grace Pod delete as the response action. Aborting gates send no response action. The experiment verifies actual disappearance for accepted actions, records any wrong-target effect, and timestamps the first observed Pod absence as a conservative upper bound on first effective deletion. It does not equate client DELETE dispatch with first actuation.

The locked planner is expected to return the two-query logical certificate:

```text
query:/facts/current_uid
query:/facts/scope_ok
```

The live Pod GET may physically provide both values in one object response; E34 is an end-to-end safety/effect experiment, not the physical-query saving benchmark.

20 repetitions are run for every DCJI/FullRefresh condition and every applicable native-precondition condition.


Runtime lock: kind v0.32.0, Kubernetes node image `kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5`, kubectl v1.36.1, workload image `nginx:1.27.5-alpine`.

## Commit-time authorization
Each trial rechecks current Kubernetes permission to delete Pods in the experiment namespace using `SelfSubjectAccessReview`. This platform-authorization check is mandatory and is recorded separately from the optimized observation certificate.
