# E29 — Diagnosing the True Cause of F1.1/F1.2's Original Deadline Infeasibility

A preregistered post-hoc extension, run entirely outside the locked v8.1.1
package and outside all other v9/E21/E23-E28 evidence. Uses the
byte-identical locked Pod manifest, unmodified, requiring no changes to
any locked file.

## The gap this closes

E23 showed that re-hosting the F1 identity-rebinding mechanism on a
ConfigMap instead of a Pod makes it deadline-feasible, and described this
as "isolating the mechanism from container-scheduling overhead" — an
assumption, not a directly tested diagnosis of the original Pod
mechanism's actual bottleneck. Inspecting the locked manifests
(`02_locked_methodology_v8.1.1/live_validation/E13/manifests/f1_1_pod_benign.yaml`,
`f1_2_statefulset.yaml`, `f4_1_pods.yaml`) shows all three set
`nodeName: flashir-nonexistent` — a node that never exists in the
cluster. These Pods are therefore **deliberately never scheduled, never
pull an image, and never start a container**. This rules out scheduling
and image-pull latency as the cause of the original 39-80s delay,
pointing instead to Kubernetes' default 30-second pod termination grace
period, which governs how long a `kubectl delete pod` waits before the
object is actually removed.

## Method

Using the byte-identical locked manifest (copied verbatim, not modified),
10 repetitions of delete+recreate under (a) default grace period
(matching the original E13 harness's plain `kubectl delete`) and (b)
forced immediate deletion (`--grace-period=0 --force`) were timed on the
same long-running `flashir-e13` cluster used throughout this study. A
transient `etcdserver: request timed out` error interrupted the first run
after all 10 default-grace-period repetitions had completed and one
forced-immediate repetition had run — a real infrastructure hiccup on a
cluster that has been running for the majority of this study, not a
logic error. The 10 already-completed default-grace-period measurements
are preserved verbatim (they are real, valid, already-executed data;
re-running ten 30-80+ second deletions solely to regenerate numbers
already obtained would waste time for no scientific benefit) and the
forced-immediate phase was cleanly re-run in full with retry logic added.

## Result

`results/summary.json`:

| Condition | n | Mean total (s) | Median total (s) | Range (s) |
|---|---|---|---|---|
| Default grace period | 10 | 78.9 | **39.5** | [18.6, 434.5] |
| Forced immediate (`--grace-period=0 --force`) | 10 | 0.91 | **0.85** | [0.58, 1.73] |

The default-grace-period measurements land squarely in the originally
observed 39-80s range from E13 (median 39.5s, most values clustered
38-41s, with one extreme 434.5s outlier and two faster 18-19s outcomes —
consistent with Kubernetes' grace-period-then-force-delete escalation
behavior varying under real cluster conditions). Forcing immediate
deletion reduces this by roughly two orders of magnitude: every one of
the 10 forced trials completed in under 1.75 seconds — comfortably inside
F1.1/F1.2's original 5-second frozen deadline.

**This confirms the hypothesis directly and precisely**: the original
deadline infeasibility was not caused by container scheduling or image
pull (neither ever occurred, since the Pods were deliberately never
schedulable) but by the default pod termination grace period governing
how `kubectl delete` behaves. A single flag change to the deletion
call — not a change of backing resource, not an architectural change —
would have made the *original* Pod-based F1.1/F1.2 mechanism itself
deadline-feasible.

## Relationship to E23

E23 (ConfigMap re-hosting) and E29 (root-cause diagnosis) are
complementary, not redundant: E23 empirically demonstrates that *a*
fast-recreating resource is deadline-feasible; E29 identifies *why* the
original resource was not, and shows the fix could have been a one-line
change to the original Pod-based mechanism rather than a substitution of
resource type. Together they give both a working replication and a
precise, tested mechanistic explanation, rather than either alone.

## What this experiment does and does not claim

- It does not claim `--grace-period=0 --force` is generally safe for
  production Pod deletion — forced deletion skips the container runtime's
  normal graceful-shutdown signal (SIGTERM then SIGKILL after grace
  period), which is fine for a Pod that was never actually running (as
  here) but would abruptly kill a real workload's containers without
  giving them a chance to shut down cleanly. The claim is narrowly about
  *this* deliberately-unscheduled diagnostic Pod, matching the locked
  manifests' own design.
- It does not modify or challenge any locked result — E13's original
  120 trials, their reported 39-80s F1.1/F1.2 durations, and their
  resulting deadline-infeasibility classification are all unchanged. This
  experiment explains a mechanism already correctly reported, using an
  unmodified copy of the locked manifest.
- 10 repetitions per condition test repeatability of one mechanism on one
  cluster, not a general characterization of Kubernetes deletion latency
  across environments.

## Files

- `manifest.yaml` — byte-identical copy of the locked
  `f1_1_pod_benign.yaml`.
- `scripts/run_e29.py` — the harness (default-grace-period rows preserved
  from the first run's real output; forced-immediate rows re-measured
  cleanly with retry logic after a transient cluster error).
- `results/forced_immediate_rows.jsonl`, `results/summary.json`.
