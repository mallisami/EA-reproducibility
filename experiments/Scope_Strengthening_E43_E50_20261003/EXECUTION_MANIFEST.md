# Execution manifest — EA run-all-six campaign

## Frozen campaign

| Item | Planned trial count | Role |
|---|---:|---|
| E43 serial cross-check | 30 | Non-pooled harness-mode sensitivity check |
| E43 | 2,504 | Grace-period sensitivity |
| E46 | 2,943 | Target-state admission vs RV binding |
| E47 | 1,140 | Retry policy |
| E48 | 1,796 | Quarantine-label PATCH action |
| E49 rep1 | 624 | Fresh-cluster replication |
| E49 rep2 | 624 | Fresh-cluster replication |
| E49 rep3 | 624 | Fresh-cluster replication |
| E49 rep4 | 624 | Fresh-cluster replication |
| E49 rep5 | 624 | Fresh-cluster replication |
| E50 | 624 | Three-control-plane-node topology |

Total planned primary experiment trials across E43/E46/E47/E48/E49/E50: **12,127**.
The separate 30-trial E43 serial cross-check is descriptive and not pooled, giving **12,157** planned recorded trials if every experiment runs.

## Frozen decision

Run all six experiments. Preserve all outcomes. Do not stop because a hypothesis is contradicted unless a pre-specified technical stop rule is triggered.

## Required returned artifacts

- `01_Experiments/results_to_send.tar.gz`
- `01_Experiments/run_all.log`

If the runner stops early, return the entire affected run directory plus `run_all.log`.

## Interpretation guardrails

- New experiments broaden/test scope; they do not retroactively invalidate the frozen pre-campaign paper.
- E49 is same-host fresh-cluster replication, not hardware/provider independence.
- E50 is same-host replicated-control-plane topology, not production HA validation.
- E43 serial cross-check is a sensitivity check, not a seventh experiment and not a gate for selectively discarding E43.
