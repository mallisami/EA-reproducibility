#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--infile',required=True); p.add_argument('--outfile',required=True); a=p.parse_args()
seen=set(); n_in=n_out=bad=0; out=Path(a.outfile); out.parent.mkdir(parents=True,exist_ok=True)
with Path(a.infile).open(encoding='utf-8',errors='replace') as f, out.open('w',encoding='utf-8') as g:
    for line in f:
        if not line.strip(): continue
        n_in+=1
        try:e=json.loads(line)
        except Exception: bad+=1; continue
        key=(e.get('auditID'),e.get('stage'),e.get('requestReceivedTimestamp'),e.get('stageTimestamp'))
        if key in seen: continue
        seen.add(key); g.write(json.dumps(e,separators=(',',':'))+'\n'); n_out+=1
print(json.dumps({'input_lines':n_in,'output_events':n_out,'bad_json_lines':bad,'PASS':bad==0},indent=2)); raise SystemExit(0 if bad==0 else 3)
