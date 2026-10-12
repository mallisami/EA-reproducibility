#!/usr/bin/env python3
"""HEADLINE_E40.txt from analysis/cells_E39.csv, exposure_E39.csv, supplement outputs, probes. usage: headline_e40.py RUN_DIR"""
import csv
import json
import sys
from pathlib import Path

rd = Path(sys.argv[1])
A, S = rd / 'analysis', rd / 'supplement'
cells = list(csv.DictReader(open(A / 'cells_E39.csv', newline='')))
expo = list(csv.DictReader(open(A / 'exposure_E39.csv', newline='')))
sup = json.load(open(S / 'SUPPLEMENT_E40.json'))
comp = list(csv.DictReader(open(S / 'comparison_kind_vs_aks_E40.csv', newline=''))) if (S / 'comparison_kind_vs_aks_E40.csv').exists() else []
sec = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(S / 'client_secondary_cells_E40.csv', newline=''))}
L = []
p = L.append
p('E40 HEADLINE - AKS replication of the E38v2/E39-A exposure-window measurement (primary oracle = client RV order)')
p('=' * 110)
p('Cells: P(INVALID_EXECUTED) k/n, Wilson 95% CI; sec = client-timestamp secondary p. Columns: primary n = analyzable + fail-closed.')
cur = None
for c in cells:
    parts = c['cell'].split('/')
    if parts[0] != 'A':
        continue
    arm, cond, d = parts[1], parts[3], parts[4]
    if (arm, cond) != cur:
        cur = (arm, cond)
        p('')
        p('%s / %s' % (arm, cond))
    s = sec.get((arm, cond, '' if d == 'NA' else str(float(d))))
    sp = '' if not s or s['sec_p'] in ('', 'None') else ' sec=%.3f' % float(s['sec_p'])
    ph = c['p_hat']
    p('  delta=%5s ms  k/n=%3s/%-3s  p=%s [%s, %s]  VALID=%s INVALID=%s CORR_ABORT=%s FALSE_ABORT=%s UID_BLOCKED=%s UNK=%s INFRA=%s%s' % (
        d, c['k_invalid_executed'], c['n_primary_denominator'], ('%.3f' % float(ph)) if ph not in ('', 'NA') else ph,
        ('%.3f' % float(c['wilson_lo'])) if c['wilson_lo'] not in ('', 'NA') else '', ('%.3f' % float(c['wilson_hi'])) if c['wilson_hi'] not in ('', 'NA') else '',
        c['n_VALID_EXECUTED'], c['n_INVALID_EXECUTED'], c['n_CORRECT_ABORT'], c['n_FALSE_ABORT'], c['n_UID_BLOCKED'],
        c['n_UNKNOWN'], c['n_INFRA_ERROR'], sp))
p('')
p('d* (smallest grid delta with p_hat <= 0.05 there and at all larger deltas), window area 0-200 ms, bootstrap CI:')
for e in expo:
    if e['experiment'] == 'A':
        p('  %-38s d*=%-10s last_exposed=%-6s area=%-8s boot95=[%s, %s] frac_not_closed=%s d*_sec=%s' % (
            e['group'], e['d_star_ms'], e['last_exposed_delta_ms'], e['window_area_ms'], e['boot_ci95_lo_ms'],
            e['boot_ci95_hi_ms'], e['boot_frac_not_closed'], e['d_star_secondary_ms']))
p('')
p('Agreement primary vs client-time secondary: %s' % json.dumps(sup['agreement_primary_vs_client_secondary']))
p('Accepted DELETEs whose writer request overlapped the DELETE request: %s' % json.dumps(sup['overlap_accepted_deletes']))
p('')
p('Webhook latency cost (DELETE round trip, accepted, ms; HL shift with bootstrap 95% CI):')
for k, v in sup['webhook_latency'].items():
    p('  %s: %s' % (k, json.dumps(v)))
p('')
p('A4d availability cost:')
for k, v in sup['a4d_availability'].items():
    p('  %s: %s' % (k, json.dumps(v)))
p('')
if comp:
    p('AKS minus kind, per cell (Newcombe hybrid-score 95% CI):')
    for r in comp:
        p('  %-17s %-3s %-28s d=%6s  AKS %s/%s  kind %s/%s  diff=%+.3f [%+.3f, %+.3f]' % (
            r['reference'], r['arm'], r['condition'], r['delta_ms'], r['k_aks'], r['n_aks'], r['k_kind'], r['n_kind'],
            float(r['diff']), float(r['newcombe_lo']), float(r['newcombe_hi'])))
open(rd / 'HEADLINE_E40.txt', 'w').write('\n'.join(L) + '\n')
print('\n'.join(L))
