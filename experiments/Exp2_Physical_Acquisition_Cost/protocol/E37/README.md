# E37 — Full independent-host E21 replication

This is the complete 12-cell E21 physical-query matrix rerun on the independent external host. It retains both acquisition modes (sequential/parallel), both load levels (idle/moderate), three conditions, 20 warm-up pairs per cell, and 3 x 100 measured pairs per cell for 3,600 measured pairs total. It is deliberately separate from E35, which is the reduced 300-pair replication.

The primary endpoint remains paired physical refresh latency. Request-count and decision correctness are independently verified from raw logs.


Runtime lock: kind v0.32.0, Kubernetes node image `kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5`, kubectl v1.36.1.
