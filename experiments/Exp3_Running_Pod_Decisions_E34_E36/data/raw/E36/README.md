# E36 — Multi-node running-workload replication

Replicates E34 on an independent physical host using a 3-node kind cluster (1 control-plane, 2 workers). The nginx target is pinned to a worker. The same 220-trial guard/condition design and end-to-end wrong-target/effect checks are retained. This tests topology robustness, not independent human validation. No LANL data are used.


Runtime lock: kind v0.32.0, Kubernetes node image `kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5`, kubectl v1.36.1, workload image `nginx:1.27.5-alpine`.

## Commit-time authorization
Each trial rechecks current Kubernetes permission to delete Pods in the experiment namespace using `SelfSubjectAccessReview`. This platform-authorization check is mandatory and is recorded separately from the optimized observation certificate.
