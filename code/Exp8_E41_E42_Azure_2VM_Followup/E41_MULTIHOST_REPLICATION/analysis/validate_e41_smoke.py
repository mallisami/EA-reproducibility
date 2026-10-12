#!/usr/bin/env python3
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--raw',required=True); p.add_argument('--expected',type=int,default=78); a=p.parse_args()
rows=[json.loads(x) for x in Path(a.raw).read_text().splitlines() if x.strip()]
ids=[r.get('trial_id') for r in rows]
checks={'record_count':len(rows)==a.expected,'unique_trial_ids':len(set(ids))==len(ids),
        'no_infra_error':all(r.get('status')!='INFRA_ERROR' for r in rows),
        'target_ready_observed':all(((r.get('gate') or {}).get('pod_state') or {}).get('ready') is not False for r in rows if r.get('gate'))}
print(json.dumps({'checks':checks,'PASS':all(checks.values()),'n':len(rows)},indent=2))
raise SystemExit(0 if all(checks.values()) else 4)
