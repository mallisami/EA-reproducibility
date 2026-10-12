#!/usr/bin/env python3
"""Reads audit JSON lines on stdin (piped via `sudo cat`), the probe client output and the webhook log.
argv: NS client_ops.json webhook_log.jsonl"""
import json, sys
sys.path.insert(0, sys.argv[4] + '/analysis')
from e38v2_common import parse_k8s_time_ns

ns, ops_p, wh_p = sys.argv[1], sys.argv[2], sys.argv[3]
ev = [json.loads(l) for l in sys.stdin if l.strip()]
mine = [e for e in ev if (e.get('objectRef') or {}).get('namespace') == ns]
print('audit events total', len(ev), 'in test ns', len(mine))
print('stages', sorted({e['stage'] for e in ev}), 'levels', sorted({e['level'] for e in ev}))
print('verbs/resources in ns', sorted({(e['verb'], e['objectRef'].get('resource')) for e in mine}))
ok = True
if any(e['stage'] == 'RequestReceived' for e in ev):
    print('FAIL RequestReceived present'); ok = False
cm_patch = [e for e in mine if e['verb'] == 'patch' and e['objectRef']['resource'] == 'configmaps'
            and e['stage'] == 'ResponseComplete' and e.get('responseObject') and e['level'] == 'RequestResponse']
pod_del = [e for e in mine if e['verb'] == 'delete' and e['objectRef']['resource'] == 'pods' and e['stage'] == 'ResponseComplete']
print('RequestResponse configmap patch events', len(cm_patch), '; pod delete events', len(pod_del))
ok &= bool(cm_patch) and bool(pod_del)
# denied (webhook) deletes: code 403 with webhook message
den = [e for e in pod_del if (e.get('responseStatus') or {}).get('code') == 403]
for e in den[:5]:
    print('403 delete:', e['objectRef']['name'], (e.get('responseStatus') or {}).get('message', '')[:160],
          'user', e['user']['username'])
# Audit-Id join
d = json.load(open(ops_p))
by = {}
for e in ev:
    by.setdefault(e['auditID'], []).append(e)
n = miss = rvmis = 0
seq = []
for o in d['ops']:
    n += 1
    es = [e for e in by.get(o['audit_id'], []) if e['stage'] == 'ResponseComplete']
    if len(es) != 1:
        miss += 1
        continue
    e = es[0]
    arv = ((e.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion')
    if o['status'] in (200, 201) and arv != o['rv']:
        rvmis += 1
    if o['status'] in (200, 201) and arv and arv.isdigit():
        seq.append((int(arv), parse_k8s_time_ns(e['stageTimestamp']), parse_k8s_time_ns(e['requestReceivedTimestamp']), o['op']))
print(f'Audit-Id join: {n - miss}/{n} client responses matched exactly one ResponseComplete event; RV mismatches {rvmis}')
ok &= miss == 0 and rvmis == 0
# RV order vs ResponseComplete timestamp order (pairwise)
seq.sort()
conc = disc = ties = 0
for i in range(len(seq)):
    for j in range(i + 1, len(seq)):
        a, b = seq[i], seq[j]
        if a[0] == b[0]:
            continue
        if a[1] == b[1]:
            ties += 1
        elif a[1] < b[1]:
            conc += 1
        else:
            disc += 1
adj_c = sum(1 for a, b in zip(seq, seq[1:]) if a[1] < b[1])
adj_t = sum(1 for a, b in zip(seq, seq[1:]) if a[1] == b[1])
print(f'RV vs stageTimestamp: {len(seq)} committed ops; pairwise concordant {conc}, discordant {disc}, timestamp ties {ties}; '
      f'adjacent-in-RV-order concordant {adj_c}/{len(seq) - 1}, ties {adj_t}')
seqc = [s for s in seq if s[3].endswith('_conc')]
dc = sum(1 for a, b in zip(seqc, seqc[1:]) if a[1] > b[1])
print(f'  concurrent burst subset: {len(seqc)} ops, adjacent discordant {dc}')
# webhook log
wh = [json.loads(l) for l in open(wh_p) if l.strip()]
print('webhook log entries', len(wh), 'pods', sorted({w['pod_name'] for w in wh}))
for w in wh:
    print('  webhook', w['pod_name'], w['pod_uid'][:8], w['decision'], w['reason'][:60], 'reads', len(w['reads']),
          'per-read ts', all('t_send_wall_ns' in r for r in w['reads']), 'numeric rv', all(r['resource_version'].isdigit() for r in w['reads']))
nolab = [w for w in wh if w['pod_name'] in ('probe-nolabel', 'probe-reset')]
print('webhook entries for probe-nolabel/probe-reset (expect 0):', len(nolab))
ok &= not nolab
print('AUDIT/PROBE CHECKS', 'OK' if ok else 'FAILED')
