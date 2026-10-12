#!/usr/bin/env python3
"""E38v2 RERUN: per-cell comparison first run vs rerun, P(INVALID_EXECUTED) primary.
Difference rerun - first with Newcombe hybrid-score 95% CI (method 10, from the two Wilson intervals).
Also d* per (arm, condition) and webhook latency. Descriptive only (no tests).
argv: FIRST_ANALYSIS_DIR RERUN_ANALYSIS_DIR OUT_CSV"""
import csv, json, math, sys

Z = 1.959963984540054


def wilson(k, n):
    if n == 0:
        return None, None, None
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z / d * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    lo, hi = max(0.0, c - h), min(1.0, c + h)
    if k == 0:
        lo = 0.0
    if k == n:
        hi = 1.0
    return p, lo, hi


def newcombe(k1, n1, k2, n2):
    p1, l1, u1 = wilson(k1, n1)
    p2, l2, u2 = wilson(k2, n2)
    if p1 is None or p2 is None:
        return None, None, None
    d = p2 - p1
    return d, d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2), d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)


fd, rd, out = sys.argv[1:4]
A = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(fd + '/cells_E38v2.csv'))}
B = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(rd + '/cells_E38v2.csv'))}
rows = []
for key in B:
    a, b = A.get(key), B[key]
    if a is None:
        continue
    k1, n1 = int(a['k_invalid_executed']), int(a['n_primary_denominator'])
    k2, n2 = int(b['k_invalid_executed']), int(b['n_primary_denominator'])
    d, lo, hi = newcombe(k1, n1, k2, n2)
    rows.append({'arm': key[0], 'condition': key[1], 'delta_ms': key[2], 'first_k': k1, 'first_n': n1,
                 'first_p': None if not n1 else k1 / n1, 'rerun_k': k2, 'rerun_n': n2, 'rerun_p': None if not n2 else k2 / n2,
                 'diff_rerun_minus_first': d, 'diff_ci95_lo': lo, 'diff_ci95_hi': hi,
                 'ci_excludes_0': (lo is not None and (lo > 0 or hi < 0)),
                 'first_unknown': a['n_UNKNOWN'], 'rerun_unknown': b['n_UNKNOWN']})
order = {'A0_native_uid': 0, 'A1_dcji': 1, 'A2_fullrefresh': 2, 'A3_dcji_webhook': 3}
corder = {'no_write': 0, 'async_scope': 1, 'async_app_policy': 2, 'async_evidence_last_literal': 3, 'uid_rebind_control': 4}
rows.sort(key=lambda r: (order[r['arm']], corder[r['condition']], -1e9 if r['delta_ms'] == 'NA' else float(r['delta_ms'])))
cols = list(rows[0].keys())
with open(out, 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=cols, lineterminator='\n')
    w.writeheader()
    for r in rows:
        w.writerow({k: ('%.4f' % v if isinstance(v, float) else v) for k, v in r.items()})
f = lambda x: '  NA ' if x is None else '%.3f' % x
print('arm | condition | delta | first k/n p | rerun k/n p | diff [95% Newcombe] | CI excl 0')
for r in rows:
    print('%s | %s | %s | %d/%d %s | %d/%d %s | %s [%s, %s] | %s' % (
        r['arm'], r['condition'], r['delta_ms'], r['first_k'], r['first_n'], f(r['first_p']), r['rerun_k'], r['rerun_n'],
        f(r['rerun_p']), f(r['diff_rerun_minus_first']), f(r['diff_ci95_lo']), f(r['diff_ci95_hi']), '*' if r['ci_excludes_0'] else ''))
print('cells compared', len(rows), 'CI excludes 0 in', sum(r['ci_excludes_0'] for r in rows))
EA = {(r['arm'], r['condition']): r for r in csv.DictReader(open(fd + '/exposure_window_E38v2.csv'))}
EB = {(r['arm'], r['condition']): r for r in csv.DictReader(open(rd + '/exposure_window_E38v2.csv'))}
print('\nEXPOSURE d* (primary): first -> rerun  [rerun bootstrap 95%]')
for k in sorted(EB, key=lambda x: (order[x[0]], corder[x[1]])):
    print('  %s %s: %s -> %s [%s, %s]; last exposed %s -> %s' % (k[0], k[1], EA[k]['d_star_ms'], EB[k]['d_star_ms'],
          EB[k]['boot_ci95_lo_ms'], EB[k]['boot_ci95_hi_ms'], EA[k]['last_exposed_delta_ms'], EB[k]['last_exposed_delta_ms']))
LA = {r['arm']: r for r in csv.DictReader(open(fd + '/webhook_latency_E38v2.csv'))}
LB = {r['arm']: r for r in csv.DictReader(open(rd + '/webhook_latency_E38v2.csv'))}
print('\nDELETE RTT (no_write, accepted) median ms: first -> rerun')
for a in LB:
    print('  %s: %s -> %s  (HL %s [%s, %s] -> %s [%s, %s])' % (a, LA[a].get('median_ms'), LB[a].get('median_ms'),
          LA[a].get('hl_ms'), LA[a].get('hl_ci95_lo_ms'), LA[a].get('hl_ci95_hi_ms'),
          LB[a].get('hl_ms'), LB[a].get('hl_ci95_lo_ms'), LB[a].get('hl_ci95_hi_ms')))
