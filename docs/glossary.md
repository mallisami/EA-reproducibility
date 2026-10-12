# Glossary and experiment map

| Package experiment | Internal study | Reader-facing role |
|---|---|---|
| Experiment 1 | E5/W2/T4-T7/E12 family | Designed-state/model verification |
| Experiment 2 | E21/E37/E35/E26/E25 | Physical acquisition cost |
| Experiment 3 | E34/E36 | Running-Pod checks |
| Experiment 4 | E38 | Constructive validation |
| Experiment 5 | E38v2 | Primary controlled-delay exposure |
| Experiment 6 | E39 A-G | Target-bound enforcement and robustness |
| Experiment 7 | E40 | Exploratory AKS sensitivity |
| Experiment 8 | E41 | Two-host K3s mechanism sensitivity |
| Experiment 9 | E42 | Benign target-churn conflict-cost study |
| Experiment 10 | A4d R2 / `a4dcr-r2-20261001` | Complete-observation direct-binding confirmation |
| Experiment 11 | E43 | Graceful-termination boundary and schedule sensitivity |
| Experiment 12 | E46 | Predicate-specific admission vs whole-object RV binding |
| Experiment 13 | E47 | Retry-policy safety/liveness |
| Experiment 14 | E48 | Conditional quarantine-label PATCH |
| Experiment 15 | E49 | Five recreated same-host clusters |
| Experiment 16 | E50 | Same-host replicated-control-plane topology |

**A1 / DCJI client gate:** selective client-side revalidation before action.  
**A2 / FullRefresh:** full client-side refresh.  
**A3:** DCJI plus validating admission recheck.  
**A4d:** direct target binding: decision-relevant target-resident state plus DELETE UID/resourceVersion conditions.  
**A4m:** mirrored target binding; source state remains external and is asynchronously mirrored to the target.  
**A5v:** predicate-specific validating-admission condition used in E46.  
**A6u / A6b:** unbound / resourceVersion-bound quarantine-label PATCH arms in E48.  
**RV:** Kubernetes `resourceVersion`.  
**INVALID_EXECUTED / stale accept:** an action took effect despite a conflicting state change under the study-specific classifier.  
**CORRECT_ABORT / benign abort:** a rejected action, with the latter reserved for security-irrelevant target churn in the corresponding studies.  
**UID_BLOCKED:** same-name replacement blocked by UID precondition.  
**EA-TOCTOU:** evidence-to-action time-of-check-to-time-of-use race.  
**Protected action boundary:** the tested API-visible boundary at which a covered target-state condition can reject the operation.  
**Later effect:** asynchronous final outcome (for example final Pod removal), which is analyzed separately from the protected action boundary.
