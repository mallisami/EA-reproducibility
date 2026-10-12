# E43–E50 scope-strengthening campaign — authoritative package note

Current publication mapping: `docs/claims_to_evidence.csv` and the package README. The campaign description below retains the historical study context and may use earlier figure/section numbers.


Campaign: `EA_RUN_ALL_SIX_20261003`  
Execution date: 2026-10-03  
Recorded trials: **12,157**  
Infrastructure errors in frozen arm summaries: **0**  
UNKNOWN classifications in frozen arm summaries: **0**

This directory stores campaign-level probes, execution logs, and frozen-plan hashes. The original post-run reviewer README has been quarantined under `provenance/Legacy_Superseded/` because it contains a known arithmetic error (`12,179` instead of `12,157`) and several phrases broader than the evidence warrants. Use this file, the per-experiment `summary.json`/`cells.csv`/`trials.jsonl` files, and `scripts/L2_verify_scope_strengthening_E43_E50.py` as the authoritative interpretation layer.

## Experiment map

- E43: graceful-termination sensitivity and protected-boundary versus later-effect distinction.
- E46: target-state admission versus whole-object resourceVersion binding.
- E47: no-retry, blind-retry, and revalidating-retry safety/liveness behavior.
  E47 contains 1,140 primary trials: 1,080 safety/liveness trials summarized in Table S36 plus 60 no-write safety controls (20 per retry policy); all 1,140 are included in the campaign total.
- E48: quarantine-label PATCH action, unbound versus resourceVersion-bound mutation.
- E49: five independently recreated same-host cluster instances.
- E50: same-host three-control-plane-node topology.

The updated Discover Computing paper includes E43-E50 in the manuscript and Supplement. Current paper locators are maintained in `docs/claims_to_evidence.csv`. The original pre-integration manuscript state is retained only in historical provenance documents.
