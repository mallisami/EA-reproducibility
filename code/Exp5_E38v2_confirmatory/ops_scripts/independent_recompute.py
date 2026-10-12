#!/usr/bin/env python3
"""E38v2 RERUN INDEPENDENT recomputation of the headline counts (does NOT import any analysis/ code).

From RAW_TRIALS.jsonl + audit.log + pod_events.jsonl only, per (arm, condition, delta):
  k      = #trials with an accepted DELETE (HTTP 200/202) whose committed writer resourceVersion (audit
           ResponseComplete responseObject.metadata.resourceVersion of the writer PATCH, joined by Audit-Id)
           is numerically smaller than the DELETE's deletion resourceVersion, where the post-write state
           violates V = ((a&b)|(c&d)|(e&f)) & scope & app (computed here from fixture.initial_values).
           Deletion RV: audit responseObject of the DELETE, else the client-recorded RV, else (plan v1.2)
           the single DELETED pod-watch event of the expected Pod UID.
           uid_rebind_control: accepted DELETE whose audited deleted UID != expected UID.
  n      = #trials with runner status COMPLETE that are dispatched with an HTTP status or not dispatched
           (gate abort / fence), INFRA_ERROR excluded; unresolvable ordering counted as n_unresolved.
  k_sec  = same as k but ordering by writer ResponseComplete < DELETE ResponseComplete (plan v1.2 secondary)
  k_ter  = same as k but ordering by writer ResponseComplete < DELETE requestReceived (v1 rule, tertiary)
Usage: independent_recompute.py RUN_DIR [cells_E38v2.csv] [max_stage] [pod_events.jsonl]
"""
import csv, json, sys
from collections import defaultdict
from datetime import datetime, timezone

rd = sys.argv[1]
cmp_csv = sys.argv[2] if len(sys.argv) > 2 else None
max_stage = int(sys.argv[3]) if len(sys.argv) > 3 else 2
pe_path = sys.argv[4] if len(sys.argv) > 4 else rd + '/pod_events.jsonl'


def ts(s):
    base, frac = s.rstrip('Z').split('.') if '.' in s else (s.rstrip('Z'), '0')
    dt = datetime.strptime(base, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000 + int(frac.ljust(6, '0')[:6])


audit = {}
with open(rd + '/audit.log') as fh:
    for line in fh:
        if line.strip():
            e = json.loads(line)
            if e.get('stage') == 'ResponseComplete':
                audit[e['auditID']] = e
deleted = defaultdict(list)
try:
    with open(pe_path) as fh:
        for line in fh:
            if line.strip():
                e = json.loads(line)
                if e.get('type') == 'DELETED' and e.get('uid'):
                    deleted[e['uid']].append(e.get('rv'))
except FileNotFoundError:
    print('no pod events file', pe_path)


def truth(v):
    return str(v).strip().lower() in ('true',)


def valid(s):
    j = (s['evidence-a'] and s['evidence-b']) or (s['evidence-c'] and s['evidence-d']) or (s['evidence-e'] and s['evidence-f'])
    return j and s['response-scope'] and s['application-policy']


cells = defaultdict(lambda: defaultdict(int))
with open(rd + '/RAW_TRIALS.jsonl') as fh:
    for line in fh:
        t = json.loads(line)
        if t['stage'] > max_stage:
            continue
        c = cells[(t['arm'], t['condition'], 'NA' if t['delta_ms'] is None else '%g' % t['delta_ms'])]
        if t['status'] == 'INFRA_ERROR' or not t['fixture'] or not t['fixture']['reset_ok']:
            c['n_infra'] += 1
            continue
        if t['writer'] is not None and not (200 <= (t['writer'].get('http_status') or 0) < 300):
            c['n_infra'] += 1
            continue
        d = t['delete']
        if not d['dispatched'] or d['http_status'] not in (200, 202):
            c['n'] += 1
            continue
        da = audit.get(d['audit_id'])
        if da is None:
            c['n_unresolved'] += 1
            continue
        dmeta = (da.get('responseObject') or {}).get('metadata') or {}
        if t['condition'] == 'uid_rebind_control':
            c['n'] += 1
            c['k'] += bool(dmeta.get('uid') and dmeta.get('uid') != d['expected_uid'])
            continue
        if t['writer'] is None:
            c['n'] += 1
            continue
        wa = audit.get(t['writer']['audit_id'])
        drv = dmeta.get('resourceVersion') or d.get('deleted_object_resource_version')
        if not drv:
            dl = deleted.get(d['expected_uid'], [])
            if len(dl) == 1:
                drv = dl[0]
                c['n_drv_from_watch'] += 1
        if wa is None or not str(drv or '').isdigit():
            c['n_unresolved'] += 1
            continue
        wrv = int(((wa.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion'))
        state = {o: truth(v) for o, v in t['fixture']['initial_values'].items() if o != 'e38-epoch'}
        state[t['writer']['object']] = truth(t['writer']['new_value'])
        inv = not valid(state)
        c['n'] += 1
        c['k'] += (wrv < int(drv)) and inv
        c['k_sec'] += (ts(wa['stageTimestamp']) < ts(da['stageTimestamp'])) and inv
        c['k_ter'] += (ts(wa['stageTimestamp']) < ts(da['requestReceivedTimestamp'])) and inv

tot = defaultdict(int)
for v in cells.values():
    for k2, x in v.items():
        tot[k2] += x
print('independent recomputation over', len(cells), 'cells:', dict(tot))
if cmp_csv:
    rows = {(r['arm'], r['condition'], r['delta_ms']): r for r in csv.DictReader(open(cmp_csv))}
    ag = defaultdict(int)
    diffs = []
    for key, v in sorted(cells.items()):
        r = rows.get(key)
        if r is None:
            diffs.append((key, 'missing in analysis'))
            continue
        for mine, theirs in (('k', 'k_invalid_executed'), ('n', 'n_primary_denominator'),
                             ('k_sec', 'sec_k_invalid_executed'), ('k_ter', 'ter_k_invalid_executed')):
            if int(r[theirs]) == v[mine]:
                ag[mine] += 1
            else:
                diffs.append((key, '%s analysis=%s independent=%d (unresolved %d)' % (mine, r[theirs], v[mine], v['n_unresolved'])))
    print('cells identical: k %d/%d; n %d/%d; k_secondary %d/%d; k_tertiary %d/%d'
          % (ag['k'], len(cells), ag['n'], len(cells), ag['k_sec'], len(cells), ag['k_ter'], len(cells)))
    for d in diffs[:60]:
        print('  DIFF', d)
