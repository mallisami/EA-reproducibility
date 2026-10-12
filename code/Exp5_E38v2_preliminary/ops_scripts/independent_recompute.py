#!/usr/bin/env python3
"""E38v2 INDEPENDENT recomputation of the headline counts (does NOT import any analysis/ code).

From RAW_TRIALS.jsonl + audit.log only, per (arm, condition, delta):
  k  = #trials with an accepted DELETE (HTTP 200/202) whose committed writer resourceVersion (audit
       ResponseComplete responseObject.metadata.resourceVersion of the writer PATCH, joined by
       Audit-Id) is numerically smaller than the DELETE's committed resourceVersion (audit
       responseObject of the DELETE), where the post-write state violates
       V = ((a&b)|(c&d)|(e&f)) & scope & app  (computed here from fixture.initial_values);
       uid_rebind_control: accepted DELETE whose audited deleted UID != expected UID.
  n  = #trials with runner status COMPLETE that are either dispatched with an HTTP status or not
       dispatched (gate ABORT_REPLAN / UNKNOWN_COHERENCE), i.e. INFRA_ERROR excluded; trials whose
       ordering cannot be established (missing audit RVs) are counted separately as n_unresolved.
Also reports a descriptive alternative time rule: writer ResponseComplete < DELETE ResponseComplete.
Usage: independent_recompute.py RUN_DIR [cells_E38v2.csv to compare] [max_stage]
"""
import csv, json, sys
from collections import defaultdict
from datetime import datetime, timezone

rd = sys.argv[1]
cmp_csv = sys.argv[2] if len(sys.argv) > 2 else None
max_stage = int(sys.argv[3]) if len(sys.argv) > 3 else 2


def ts(s):
    # RFC3339 with microseconds -> integer microseconds
    base, frac = s.rstrip('Z').split('.') if '.' in s else (s.rstrip('Z'), '0')
    dt = datetime.strptime(base, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000 + int(frac.ljust(6, '0')[:6])


audit = {}
with open(rd + '/audit.log') as fh:
    for line in fh:
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get('stage') == 'ResponseComplete':
            audit[e['auditID']] = e

TRUE = {'true'}


def truth(v):
    return str(v).strip().lower() in TRUE


def valid(state):
    j = (state['evidence-a'] and state['evidence-b']) or (state['evidence-c'] and state['evidence-d']) or \
        (state['evidence-e'] and state['evidence-f'])
    return j and state['response-scope'] and state['application-policy']


cells = defaultdict(lambda: {'k': 0, 'n': 0, 'n_unresolved': 0, 'n_infra': 0, 'k_alt_time': 0})
with open(rd + '/RAW_TRIALS.jsonl') as fh:
    for line in fh:
        t = json.loads(line)
        if t['stage'] > max_stage:
            continue
        key = (t['arm'], t['condition'], 'NA' if t['delta_ms'] is None else '%g' % t['delta_ms'])
        c = cells[key]
        if t['status'] == 'INFRA_ERROR' or not t['fixture'] or not t['fixture']['reset_ok']:
            c['n_infra'] += 1
            continue
        if t['writer'] is not None and not (200 <= (t['writer'].get('http_status') or 0) < 300):
            c['n_infra'] += 1
            continue
        d = t['delete']
        if not d['dispatched']:
            c['n'] += 1          # gate abort or fenced: no accepted effect
            continue
        if d['http_status'] not in (200, 202):
            c['n'] += 1          # denied (webhook 403) or UID 409: no accepted effect on the right object
            continue
        da = audit.get(d['audit_id'])
        if da is None:
            c['n_unresolved'] += 1
            continue
        dmeta = (da.get('responseObject') or {}).get('metadata') or {}
        if t['condition'] == 'uid_rebind_control':
            c['n'] += 1
            if dmeta.get('uid') and dmeta.get('uid') != d['expected_uid']:
                c['k'] += 1
            continue
        if t['writer'] is None:
            c['n'] += 1
            continue
        wa = audit.get(t['writer']['audit_id'])
        if wa is None or not str(dmeta.get('resourceVersion', '')).isdigit():
            c['n_unresolved'] += 1
            continue
        wrv = int(((wa.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion'))
        drv = int(dmeta['resourceVersion'])
        state = {o: truth(v) for o, v in t['fixture']['initial_values'].items() if o != 'e38-epoch'}
        state[t['writer']['object']] = truth(t['writer']['new_value'])
        c['n'] += 1
        if wrv < drv and not valid(state):
            c['k'] += 1
        if ts(wa['stageTimestamp']) < ts(da['stageTimestamp']) and not valid(state):
            c['k_alt_time'] += 1

tot = defaultdict(int)
for v in cells.values():
    for k2, x in v.items():
        tot[k2] += x
print('independent recomputation over', len(cells), 'cells:', dict(tot))
if cmp_csv:
    rows = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(cmp_csv))}
    agree_k = agree_n = 0
    diffs = []
    for key, v in sorted(cells.items()):
        r = rows.get(key)
        if r is None:
            diffs.append((key, 'missing in analysis'))
            continue
        ka = int(r['k_invalid_executed'])
        na = int(r['n_primary_denominator'])
        if ka == v['k']:
            agree_k += 1
        else:
            diffs.append((key, 'k analysis=%d independent=%d' % (ka, v['k'])))
        if na == v['n']:
            agree_n += 1
        else:
            diffs.append((key, 'n analysis=%d independent=%d (unresolved %d)' % (na, v['n'], v['n_unresolved'])))
    print('cells with identical INVALID_EXECUTED count k: %d/%d; identical denominator n: %d/%d'
          % (agree_k, len(cells), agree_n, len(cells)))
    for d in diffs[:60]:
        print('  DIFF', d)
    print('descriptive: k by alternative time rule (writer RC < DELETE RC) total', tot['k_alt_time'], 'vs RV rule', tot['k'])
