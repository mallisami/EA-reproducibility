#!/usr/bin/env python3
"""E38v2 delta-sweep sizing from E38 smoke timing (no cluster access)."""
import json, math
from pathlib import Path
import numpy as np, pandas as pd
BASE = Path.home() / 'dcji_reanalysis_20260926'
OUT = BASE / 'out/e38'
ver = json.load(open(OUT / 'E38_VERIFICATION.json'))
z = 1.959963984540054

def wilson_hw(x, n):
    p = x / n
    return z / (1 + z * z / n) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
def wilson_ci(x, n):
    p = x / n; c = (p + z * z / (2 * n)) / (1 + z * z / n); h = wilson_hw(x, n)
    return c - h, c + h
def worst_hw(n):  # max over all achievable x
    return max(wilson_hw(x, n) for x in range(n + 1))
def n_for(target):
    n = 2
    while worst_hw(n) > target:
        n += 1
    return n
n10, n15 = n_for(0.10), n_for(0.15)
n_zero_ub05 = next(n for n in range(2, 1000) if wilson_ci(0, n)[1] <= 0.05)
n_zero_ub10 = next(n for n in range(2, 1000) if wilson_ci(0, n)[1] <= 0.10)
sz = dict(z=z, n_worst_hw_le_0_10=n10, worst_hw_at_n10=worst_hw(n10), n_worst_hw_le_0_15=n15, worst_hw_at_n15=worst_hw(n15),
          hw_at_p0_n15=wilson_hw(0, n15), ub_at_0_of_n15=wilson_ci(0, n15)[1], ub_at_0_of_n10=wilson_ci(0, n10)[1],
          n_for_zero_events_upper_bound_le_0_05=n_zero_ub05, n_for_zero_events_upper_bound_le_0_10=n_zero_ub10)

t = ver['timing']
s_std = t['mean_s_per_trial_excl_uid_rebind_conditions']; s_uid = t['mean_s_per_trial_uid_rebind_conditions']
deltas = [-5, -2, -1, 0, 0.5, 1, 2, 3, 5, 10, 20, 50]
arms = ['A1_DCJI_client_gate', 'A2_FullRefresh_client_gate', 'A3_DCJI_plus_validating_webhook']
async_conds = ['pg_scope_single_PATCH', 'pg_app_policy_single_PATCH', 'pg_evidence_last_literal_single_PATCH']
rows = []
def plan(label, n_main, topup_points=0, n_top=None):
    main = len(arms) * len(async_conds) * len(deltas) * n_main
    top = len(arms) * len(async_conds) * topup_points * ((n_top or n_main) - n_main)
    ctrl_stable = len(arms) * n_main                      # no-write control per arm
    ctrl_uid = len(arms) * 3 * 10                         # async UID-rebind positive control, delta in {0,2,10}, 10 reps (4 s trials)
    std_trials = main + top + ctrl_stable
    secs = std_trials * s_std + ctrl_uid * s_uid
    rows.append(dict(design=label, n_per_cell=n_main, topup_delta_points=topup_points, n_topup=n_top,
                     main_trials=main, topup_trials=top, stable_ctrl=ctrl_stable, uid_ctrl=ctrl_uid,
                     total_trials=std_trials + ctrl_uid, est_hours=secs / 3600,
                     est_hours_with_15pct_overhead=secs * 1.15 / 3600))
plan('uniform_hw<=0.15', n15)
plan('uniform_hw<=0.10', n10)
plan('two_stage: hw<=0.15 all, top-up to hw<=0.10 at 3 transition deltas', n15, 3, n10)
plan('minimal: user grid 8 deltas (0..50) hw<=0.15', n15)  # recomputed below with 8 deltas
rows[-1].update(main_trials=len(arms) * len(async_conds) * 8 * n15)
rows[-1]['total_trials'] = rows[-1]['main_trials'] + rows[-1]['stable_ctrl'] + rows[-1]['uid_ctrl']
secs = (rows[-1]['main_trials'] + rows[-1]['stable_ctrl']) * s_std + rows[-1]['uid_ctrl'] * s_uid
rows[-1]['est_hours'] = secs / 3600; rows[-1]['est_hours_with_15pct_overhead'] = secs * 1.15 / 3600
P = pd.DataFrame(rows)
P.to_csv(OUT / 'E38V2_SIZING.csv', index=False)
M = pd.DataFrame([dict(arm=a, condition=c, delta_ms=d, n_stage1=n15) for a in arms for c in async_conds for d in deltas])
M.to_csv(OUT / 'E38V2_MATRIX_PROPOSAL.csv', index=False)
sz.update(sec_per_trial_std=s_std, sec_per_trial_uid=s_uid, deltas=deltas, arms=arms, async_conditions=async_conds)
json.dump(sz, open(OUT / 'E38V2_SIZING.json', 'w'), indent=2)
print(json.dumps(sz, indent=1)); print(P.to_string())
