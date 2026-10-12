import json
from pathlib import Path
import numpy as np, pandas as pd
BASE = Path.home() / 'dcji_reanalysis_20260926'
rows = [json.loads(l) for l in open(BASE / 'inputs/e38raw/RAW_TRIALS.jsonl') if l.strip()]
out = []
for r in rows:
    g = r.get('guard') or {}
    if g.get('refresh_latency_ms') is None:
        continue
    inj = 0.0
    if r['condition'] == 'during_acquisition':
        e = r['events'][-1]; inj = e['finished_ms'] - e['started_ms']
    out.append(dict(method=r['method'], cond=r['condition'], refresh=g['refresh_latency_ms'] - inj, gate=g['gate_latency_ms'] - inj, inj=inj))
D = pd.DataFrame(out)
res = {}
for m in ('DCJI', 'FullRefresh'):
    S = D[D.method == m]
    res[m] = dict(n=len(S), refresh_mean=S.refresh.mean(), refresh_median=S.refresh.median(), refresh_min=S.refresh.min(), refresh_max=S.refresh.max(),
                  gate_mean=S.gate.mean(), gate_median=S.gate.median(), gate_min=S.gate.min(), gate_max=S.gate.max(),
                  during_acq_refresh_minus_injector=S[S.cond == 'during_acquisition'].refresh.round(3).tolist())
eff = [r for r in rows if (r.get('effect') or {}).get('effect_verified')]
res['effect_verified_trials'] = len(eff)
res['effect_verified_by_method'] = pd.Series([r['method'] for r in eff]).value_counts().to_dict()
json.dump(res, open(BASE / 'out/e38/E38_LATENCY_INJECTOR_SUBTRACTED.json', 'w'), indent=2)
print(json.dumps(res, indent=1))
