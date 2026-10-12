import json, sys, statistics as st
from collections import defaultdict
for rd in sys.argv[1:]:
    raw = sorted((json.loads(l) for l in open(rd + '/RAW_TRIALS.jsonl')), key=lambda r: r['trial_id'])
    # trial duration ~ next trial's gate start - this trial's gate start (includes reset of next trial:
    # use difference of consecutive gate starts, attributed to the NEXT trial's reset + this trial's tail)
    d = defaultdict(list)
    for a, b in zip(raw, raw[1:]):
        if a['gate'] and b['gate']:
            dur = (b['gate']['t_start_mono_ns'] - a['gate']['t_start_mono_ns']) / 1e9
            k = 'uid' if a['condition'] == 'uid_rebind_control' else ('writer' if a['writer'] else 'no_write')
            d[k].append(dur)
    out = {k: (len(v), round(st.mean(v), 4), round(st.median(v), 4)) for k, v in d.items()}
    print(rd.split('/')[-1], 'n/mean/median s per trial by class:', out)
