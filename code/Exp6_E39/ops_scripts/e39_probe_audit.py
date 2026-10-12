#!/usr/bin/env python3
"""E39 probe audit checks. Reads audit JSON lines on stdin (via `sudo cat`), argv: NS PROBES_JSON OUT_JSON.
Checks: coverage (configmaps patch, pods patch by writer + mirror, pods/status by kubelet with responseObject,
pods delete 200 + 409 with message, networkpolicies create), no RequestReceived stage, e39-load / e39-system /
kube-system not logged, Audit-Id join of every probe op (exactly one ResponseComplete, RV agrees),
RV vs stageTimestamp ordering, kubelet pods/status writes per probe Pod."""
import json, sys
from datetime import datetime, timezone

ns, pj, outp = sys.argv[1], sys.argv[2], sys.argv[3]


def tns(s):
    # RFC3339 with microseconds
    d = datetime.strptime(s.replace('Z', '+0000'), '%Y-%m-%dT%H:%M:%S.%f%z')
    return int(d.timestamp()) * 10**9 + d.microsecond * 1000


ev = [json.loads(l) for l in sys.stdin if l.strip()]
P = json.load(open(pj))
t_lo = P['t_start_wall_ns'] - 5 * 10**9
t_hi = P['t_end_wall_ns'] + 5 * 10**9
out = {'audit_events_total': len(ev)}
mine = [e for e in ev if (e.get('objectRef') or {}).get('namespace') == ns]
out['events_in_ns'] = len(mine)
out['stages'] = sorted({e['stage'] for e in ev})
out['levels'] = sorted({e['level'] for e in ev})
out['ns_seen'] = sorted({(e.get('objectRef') or {}).get('namespace') or '' for e in ev})
cov = {}
for e in mine:
    if e['stage'] != 'ResponseComplete':
        continue
    o = e['objectRef']
    key = '%s %s%s %s' % (e['verb'], o.get('resource'), ('/' + o['subresource']) if o.get('subresource') else '',
                          e['user']['username'].replace('system:serviceaccount:e39-system:', 'sa:'))
    c = cov.setdefault(key, {'n': 0, 'level': set(), 'with_responseObject': 0, 'codes': set()})
    c['n'] += 1
    c['level'].add(e['level'])
    c['codes'].add((e.get('responseStatus') or {}).get('code'))
    if e.get('responseObject'):
        c['with_responseObject'] += 1
out['coverage'] = {k: dict(v, level=sorted(v['level']), codes=sorted(x for x in v['codes'] if x is not None))
                   for k, v in sorted(cov.items())}


def has(pred):
    return any(pred(k, v) for k, v in out['coverage'].items())


checks = {
    'configmaps_patch_RR': has(lambda k, v: k.startswith('patch configmaps ') and 'RequestResponse' in v['level'] and v['with_responseObject']),
    'pods_patch_writer_RR': has(lambda k, v: k == 'patch pods sa:e39-writer' and v['with_responseObject']),
    'pods_patch_mirror_RR': has(lambda k, v: k == 'patch pods sa:e39-mirror' and v['with_responseObject']),
    'pods_status_kubelet_RR': has(lambda k, v: k.startswith(('patch pods/status system:node', 'update pods/status system:node'))
                                  and 'RequestResponse' in v['level'] and v['with_responseObject']),
    'pods_delete_200': has(lambda k, v: k.startswith('delete pods ') and 200 in v['codes']),
    'pods_delete_409': has(lambda k, v: k.startswith('delete pods ') and 409 in v['codes']),
    'pods_delete_403': has(lambda k, v: k.startswith('delete pods ') and 403 in v['codes']),
    'netpol_create_RR': has(lambda k, v: k.startswith('create networkpolicies ') and v['with_responseObject']),
    'no_RequestReceived': 'RequestReceived' not in out['stages'],
    'no_load_ns_events': not any((e.get('objectRef') or {}).get('namespace') == 'e39-load' for e in ev),
    'no_system_ns_events': not any((e.get('objectRef') or {}).get('namespace') in ('e39-system', 'kube-system') for e in ev),
}
# 409 message in audit
m409 = [e for e in mine if e['verb'] == 'delete' and (e.get('responseStatus') or {}).get('code') == 409
        and e['stage'] == 'ResponseComplete']
out['delete_409_messages'] = [(e['objectRef'].get('name'), (e.get('responseStatus') or {}).get('message')) for e in m409]
checks['delete_409_message_in_audit'] = bool(m409) and all('in the precondition' in (m or '') for _, m in out['delete_409_messages'])
# Audit-Id join
by = {}
for e in ev:
    by.setdefault(e['auditID'], []).append(e)
n = miss = rvmis = 0
seq = []
misses = []
for o in P['ops']:
    if not o.get('audit_id') or o['op'] in ('create_pod', 'go_get'):
        continue   # pod CREATE and GETs are not audited (policy level None), by design
    n += 1
    es = [e for e in by.get(o['audit_id'], []) if e['stage'] == 'ResponseComplete']
    if len(es) != 1:
        miss += 1
        misses.append(o)
        continue
    e = es[0]
    arv = ((e.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion')
    if o.get('status') in (200, 201) and o.get('rv') and arv != o['rv'] and o['op'] not in ('go_get',):
        rvmis += 1
    if o['op'].startswith('order') and o.get('status') == 200 and arv and arv.isdigit():
        seq.append((int(arv), tns(e['stageTimestamp']), o['op']))
out['audit_join'] = {'ops_with_audit_id': n, 'matched': n - miss, 'rv_mismatch': rvmis, 'misses': misses[:10]}
checks['audit_join_all'] = miss == 0 and rvmis == 0
ops_no_id = [o for o in P['ops'] if not o.get('audit_id')]
out['ops_without_audit_id'] = [(o['op'], o.get('status')) for o in ops_no_id][:20]
seq.sort()
conc = disc = ties = 0
for i in range(len(seq)):
    for j in range(i + 1, len(seq)):
        a, b = seq[i], seq[j]
        if a[1] == b[1]:
            ties += 1
        elif a[1] < b[1]:
            conc += 1
        else:
            disc += 1
adj = sum(1 for a, b in zip(seq, seq[1:]) if a[1] <= b[1])
out['rv_time_order'] = {'n': len(seq), 'pairwise_concordant': conc, 'discordant': disc, 'ties': ties,
                        'adjacent_nondecreasing': adj, 'adjacent_total': max(len(seq) - 1, 0),
                        'seq_only_adjacent_nondecreasing': sum(1 for a, b in zip([s for s in seq if s[2] == 'order_seq'],
                                                                                 [s for s in seq if s[2] == 'order_seq'][1:]) if a[1] <= b[1]),
                        'seq_only_n': sum(1 for s in seq if s[2] == 'order_seq')}
# kubelet pods/status writes per probe pod
kl = {}
for e in mine:
    o = e['objectRef']
    if e['stage'] == 'ResponseComplete' and o.get('resource') == 'pods' and o.get('subresource') == 'status':
        kl.setdefault(o.get('name'), []).append({'verb': e['verb'], 'user': e['user']['username'],
                                                 'rv': ((e.get('responseObject') or {}).get('metadata') or {}).get('resourceVersion'),
                                                 't': e['stageTimestamp']})
out['pods_status_writes_per_pod'] = {k: len(v) for k, v in sorted(kl.items())}
out['pods_status_users'] = sorted({x['user'] for v in kl.values() for x in v})
out['checks'] = checks
out['PASS'] = all(checks.values())
json.dump(out, open(outp, 'w'), indent=1, default=str)
print(json.dumps({'checks': checks, 'audit_join': {k: v for k, v in out['audit_join'].items() if k != 'misses'},
                  'rv_time_order': out['rv_time_order'], 'pods_status_writes_per_pod': out['pods_status_writes_per_pod'],
                  'delete_409_messages': out['delete_409_messages'][:4]}, indent=1, default=str))
print('coverage:')
for k, v in out['coverage'].items():
    print('  %-60s n=%-5d %s RO=%d codes=%s' % (k, v['n'], v['level'], v['with_responseObject'], v['codes']))
print('PROBE AUDIT', 'PASS' if out['PASS'] else 'FAIL')
