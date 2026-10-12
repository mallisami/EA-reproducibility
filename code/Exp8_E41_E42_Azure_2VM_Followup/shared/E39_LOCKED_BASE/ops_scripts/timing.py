#!/usr/bin/env python3
"""Per-experiment seconds per trial from consecutive RAW records (monotonic anchors). Usage: timing.py RAW"""
import json, statistics, sys, collections

raw = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]


def anchor(t):
    for path in (('gate', 't_start_mono_ns'), ('acquisition', 't_start_mono_ns'), ('delete', 't_send_mono_ns')):
        v = (t.get(path[0]) or {}).get(path[1])
        if isinstance(v, int) and v > 0:
            return v
    fx = t.get('fixture') or {}
    pr = fx.get('pod_ready') or {}
    for k in ('t_ready_mono_ns', 't_start_mono_ns'):
        if isinstance(pr.get(k), int):
            return pr[k]
    return None


d = collections.defaultdict(list)
sub = collections.defaultdict(list)
for a, b in zip(raw, raw[1:]):
    x, y = anchor(a), anchor(b)
    if x and y and a['experiment'] == b['experiment'] and y > x:
        d[a['experiment']].append((y - x) / 1e9)
        key = (a['experiment'], a['arm'], a.get('condition') if a['experiment'] == 'A' else a.get('injected_latency_ms'))
        sub[key].append((y - x) / 1e9)
for x, v in sorted(d.items()):
    print('%s n=%d mean=%.3f median=%.3f max=%.2f' % (x, len(v), statistics.fmean(v), statistics.median(v), max(v)))
for k, v in sorted(sub.items(), key=lambda kv: str(kv[0])):
    if k[0] in ('A', 'C'):
        print('  ', k, 'n=%d mean=%.3f' % (len(v), statistics.fmean(v)))
