#!/usr/bin/env python3
import csv,json,sys
from pathlib import Path
if len(sys.argv)!=2:
    raise SystemExit('usage: verify_against_completed_R2.py RESULTS_DIR')
r=Path(sys.argv[1])
s=json.loads((r/'A4D_CONFIRMATION_SUMMARY.json').read_text())
rows=list(csv.DictReader((r/'analysis_a4d/cells_E39.csv').open()))
a=[x for x in rows if x.get('experiment')=='A' and len(x.get('cell','').split('/'))>=5 and x['cell'].split('/')[1]=='A4d']
assert len(a)==26, len(a)
assert sum(int(x['n_primary_denominator']) for x in a)==985
assert s['totals']=={'n':985,'invalid':0,'correct_abort':0,'false_abort':0,'unknown':0,'rv409':0},s['totals']
assert len(s['cells'])==26
print('completed R2 summary regression PASS')
