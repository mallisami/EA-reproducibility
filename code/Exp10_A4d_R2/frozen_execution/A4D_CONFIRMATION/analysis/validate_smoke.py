#!/usr/bin/env python3
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--raw',required=True); p.add_argument('--expected',type=int,default=26); a=p.parse_args(); rows=[json.loads(x) for x in Path(a.raw).read_text().splitlines() if x.strip()]
checks={'record_count':len(rows)==a.expected,'unique_ids':len({r.get('trial_id') for r in rows})==len(rows),'a4d_only':all(r.get('arm')=='A4d_commit_direct' for r in rows),'no_infra_error':all(r.get('status')!='INFRA_ERROR' for r in rows)}
print(json.dumps({'checks':checks,'PASS':all(checks.values()),'n':len(rows)},indent=2)); raise SystemExit(0 if all(checks.values()) else 4)
