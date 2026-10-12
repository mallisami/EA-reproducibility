# E35 — Independent-physical-host reduced E21 replication

E35 runs the existing six-ConfigMap F5 physical-query structure on the friend's different physical machine.

Frozen design:

- idle load;
- sequential acquisition;
- `stable`, `ea_attack`, `irrelevant_mutation`;
- 20 discarded warm-up pairs per condition;
- 100 measured randomized-order pairs per condition;
- 300 measured pairs total;
- DCJI must physically issue 3 GETs and FullRefresh 6;
- decisions are checked against the specification-derived expected outcome and against each other;
- refresh-latency differences are summarized overall, by condition, by guard order, and condition × guard order;
- gate misses are reported for 25, 50, 100, 250 and 500 ms.

The primary scientific replication target is the request-count/safety structure and the direction of the randomized-order latency result, not exact equality with V26's millisecond effect size.


Runtime lock: kind v0.32.0, Kubernetes node image `kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5`, kubectl v1.36.1.
