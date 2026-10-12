# E52 — forced pre-boundary contests (tight bound)
Purpose: tighten the 0/42 (Wilson upper 8.4%) direct-binding bound by accumulating many more
genuine precondition contests. Design: filter experiment-A A4d async cells at small deltas
{0,0.5,1,2,3} ms (+controls) and REPLICATE ×16 -> 10,144 trials, 9,360 async small-delta A4d.
Contest yield ~3.3% (as in E39) -> ~309 expected contests. If 0 stale among ~300, Wilson upper
falls to ~1.2%. This is an honest replication design (NOT a new timing primitive). Contest landing
is confirmed POST-HOC by analyze_E52_contests.py from recorded writer/delete fields, which emits
fence_diagnostics.jsonl + E52_contest_summary.json. Validated offline: schedule accepted by runner,
lock fill+verify, analyzer runs. ON-VM: the run. ~2.2 h of trials.
