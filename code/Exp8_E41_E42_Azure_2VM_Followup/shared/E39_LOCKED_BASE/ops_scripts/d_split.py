#!/usr/bin/env python3
"""EXPLORATORY (post-LOCK, not pre-registered; requested by the study coordinator): E39-D split by the
user-requested stop of the unrelated kind clusters (2026-09-26T18:40:42.881Z .. 18:41:17.593Z).
Per-trial time = gate.t_end_wall_ns (fallback delete.t_send_wall_ns). Classes from the final oracle table
(primary rule; denominator = analysable + fail-closed, as plan section 4). Wilson 95% intervals.
Usage: d_split.py RAW ORACLE_CSV OUT_TXT"""
import csv, json, math, statistics, sys
from datetime import datetime, timezone

T0 = int(datetime(2026, 9, 26, 18, 40, 42, 881000, tzinfo=timezone.utc).timestamp() * 1e9)
T1 = int(datetime(2026, 9, 26, 18, 41, 17, 593000, tzinfo=timezone.utc).timestamp() * 1e9)
raw = {}
for l in open(sys.argv[1]):
    if l.strip():
        t = json.loads(l)
        if t['experiment'] in ('D', 'C'):
            raw[t['trial_id']] = t
rows = [r for r in csv.DictReader(open(sys.argv[2])) if r['experiment'] == 'D']


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def ts(t):
    g = (t.get('gate') or {}).get('t_end_wall_ns') or (t.get('delete') or {}).get('t_send_wall_ns')
    return g


def period(x):
    if x is None:
        return 'unknown_time'
    return 'before_stop' if x < T0 else ('during_stop' if x <= T1 else 'after_stop')


agg = {}
host = {}
for r in rows:
    t = raw[int(r['trial_id'])]
    per = period(ts(t))
    key = (r['arm'], r['load_level'], per)
    a = agg.setdefault(key, {'k': 0, 'n': 0, 'excl': 0})
    cls = r['primary_class']
    if cls in ('UNKNOWN',) and 'gate_unknown_coherence' not in r['primary_reason']:
        a['excl'] += 1
    elif cls == 'INFRA_ERROR':
        a['excl'] += 1
    else:
        a['n'] += 1
        a['k'] += cls == 'INVALID_EXECUTED'
    h = host.setdefault((r['load_level'], per), [])
    cpu = (t.get('host') or {}).get('cpu_pct')
    if cpu is not None:
        h.append(cpu)
out = ['E39-D EXPLORATORY split by the stop of the unrelated clusters (post-LOCK; not pre-registered)',
       'stop window %s .. %s (UTC ns %d .. %d)' % ('2026-09-26T18:40:42.881Z', '18:41:17.593Z', T0, T1), '']
out.append('%-18s %-9s %-12s %8s  %-26s %s' % ('arm', 'load', 'period', 'k/n', 'p [Wilson 95%]', 'excluded'))
for key in sorted(agg):
    a = agg[key]
    p, lo, hi = wilson(a['k'], a['n'])
    out.append('%-18s %-9s %-12s %4d/%-4d %s %d' % (key[0], key[1], key[2], a['k'], a['n'],
               ('%.3f [%.3f, %.3f]' % (p, lo, hi)) if p is not None else 'NA', a['excl']))
out.append('')
out.append('host CPU % (per-trial snapshot) by scheduled load x period: median / p95 / n')
for key in sorted(host):
    v = sorted(host[key])
    out.append('  %-9s %-12s %.1f / %.1f / %d' % (key[0], key[1], statistics.median(v), v[int(0.95 * (len(v) - 1))], len(v)))
# pooled per load level (both arms) before vs after
out.append('')
out.append('per-delta k/n by arm x load, before_stop vs after_stop:')
cell = {}
for r in rows:
    t = raw[int(r['trial_id'])]
    per = period(ts(t))
    if per not in ('before_stop', 'after_stop'):
        continue
    if r['primary_class'] in ('INFRA_ERROR',) or (r['primary_class'] == 'UNKNOWN' and 'gate_unknown_coherence' not in r['primary_reason']):
        continue
    c = cell.setdefault((r['arm'], r['load_level'], float(r['delta_ms'])), {'before_stop': [0, 0], 'after_stop': [0, 0]})
    c[per][1] += 1
    c[per][0] += r['primary_class'] == 'INVALID_EXECUTED'
for key in sorted(cell):
    b, a = cell[key]['before_stop'], cell[key]['after_stop']
    if a[1] == 0:
        continue
    out.append('  %-18s %-9s delta=%-6s before %d/%d  after %d/%d' % (key[0], key[1], key[2], b[0], b[1], a[0], a[1]))
c_times = [ts(t) for t in raw.values() if t['experiment'] == 'C' and ts(t)]
nC = sum(1 for t in raw.values() if t['experiment'] == 'C')
out.append('')
out.append('E39-C: %d trials; trials with a gate wall stamp before the stop: %d' % (nC, sum(1 for x in c_times if x < T0)))
open(sys.argv[3], 'w').write('\n'.join(out) + '\n')
print('\n'.join(out))
