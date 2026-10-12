#!/usr/bin/env python3
"""EXPLORATORY (not pre-registered): where do A4d INVALID_EXECUTED writes commit relative to the two DELETE phases?
Phase 1 = deletion accepted (first pod-watch MODIFIED event of the target uid with deletionTimestamp; RV R_acc);
phase 2 = object removed (DELETE response RV / pod-watch DELETED RV = action RV used by the primary oracle).
usage: a4d_mechanism.py RUN_DIR"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

rd = Path(sys.argv[1])
raw = {json.loads(l)['trial_id']: json.loads(l) for l in open(rd / 'RAW_TRIALS.jsonl') if l.strip()}
orc = list(csv.DictReader(open(rd / 'analysis' / 'oracle_trials_E39.csv', newline='')))
acc, dele = {}, defaultdict(list)
for l in open(rd / 'pod_events.jsonl'):
    e = json.loads(l)
    if e.get('type') == 'MODIFIED' and e.get('deletion_timestamp') and e.get('uid') not in acc:
        acc[e['uid']] = int(e['rv'])
    if e.get('type') == 'DELETED':
        dele[e.get('uid')].append(int(e['rv']))
out = defaultdict(Counter)
for r in orc:
    arm = r['arm']
    if r['condition'] not in ('async_scope', 'async_evidence_last_literal') or r['primary_class'] != 'INVALID_EXECUTED':
        continue
    t = raw[int(r['trial_id'])]
    uid = (t.get('delete') or {}).get('expected_uid')
    w = int(r['first_write_rv']) if r['first_write_rv'] else None
    a = int(r['action_rv']) if r['action_rv'] else None
    ra = acc.get(uid)
    if w is None or ra is None:
        k = 'deletion_accept_rv_unavailable'
    elif w < ra:
        k = 'write_before_deletion_accepted (true pre-commit exposure)'
    else:
        k = 'write_after_deletion_accepted_before_removal (terminating-Pod window)'
    out[arm][k] += 1
    out[arm]['n'] += 1
res = {a: dict(c) for a, c in out.items()}
json.dump(res, open(rd / 'supplement' / 'A4D_MECHANISM_EXPLORATORY.json', 'w'), indent=1)
print(json.dumps(res, indent=1))
