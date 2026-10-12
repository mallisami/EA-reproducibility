# Code map

Experiment code is retained under package-relative paths and separated from post-run utilities.

- `Exp1_*` through `Exp7_E40/`: historical/primary code trees retained from the validated base.
- `Exp8_E41_E42_Azure_2VM_Followup/`: prospective two-VM K3s package used for E41/E42, including the locked E39 analysis base, E41/E42 schedules/locks, runner/deployment scripts and the E41 sensitivity recomputation helper.
- `Exp10_A4d_R2/frozen_execution/`: exact prospective A4d R2 execution package used for `a4dcr-r2-20261001`.
- `Exp10_A4d_R2/postrun_maintenance/`: only the later derived-summary and return-manifest utility fixes. These do not alter the scientific runner, oracle, schedules, locks or raw evidence.
- `Exp17_E52_Forced_Contest_Replication/`: E52 schedule helper, the original Scope-2 timing-only diagnostic retained for provenance, and the paper-facing analyzer that reuses the unchanged frozen E39 oracle.

Offline replay entry points are under `05_Reproduce/`.
