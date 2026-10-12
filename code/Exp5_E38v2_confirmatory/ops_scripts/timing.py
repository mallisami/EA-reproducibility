#!/usr/bin/env python3
"""per-trial wall time by trial type (start of reset -> start of next trial's reset), from RAW. argv: RAW"""
import json, statistics as st, sys
from collections import defaultdict
raw = sorted((json.loads(l) for l in open(sys.argv[1]) if l.strip()), key=lambda r: r['trial_id'])
starts = [r['fixture']['pod_ready']['t_wait_start_mono_ns'] if r.get('fixture') and r['fixture'].get('pod_ready') else None for r in raw]
by = defaultdict(list)
for i in range(len(raw) - 1):
    if starts[i] and starts[i + 1]:
        c = raw[i]['condition']
        k = 'uid' if c == 'uid_rebind_control' else ('writer' if raw[i]['writer'] else 'no_write')
        by[k].append((starts[i + 1] - starts[i]) / 1e9)
for k, v in by.items():
    print(k, 'n', len(v), 'mean %.3f median %.3f' % (st.mean(v), st.median(v)))
