import json, sys
sys.path.insert(0, sys.argv[2] + '/analysis')
from e38v2_common import parse_k8s_time_ns
d = json.load(open(sys.argv[1]))
ops = d['ops']
ev = {}
for l in sys.stdin:
    if l.strip():
        e = json.loads(l)
        ev[e['auditID']] = e
# phase 1 sequential: print first 13 ops in client order with rv and stage ts
for o in ops[:14]:
    e = ev.get(o['audit_id'])
    ro = (e or {}).get('responseObject') or {}
    md = ro.get('metadata') or {}
    print(o['op'], o['status'], 'rv', o['rv'], 'audit rv', md.get('resourceVersion'), 'uid', (md.get('uid') or '')[:8],
          'delTS', md.get('deletionTimestamp'), 'recv', e['requestReceivedTimestamp'][11:], 'done', e['stageTimestamp'][11:])
# for deletes: compare RV with max RV of ops that completed before the delete was received
dels = [o for o in ops if o['op'].startswith('delete') and o['status'] == 200]
com = [(int(o['rv']), parse_k8s_time_ns(ev[o['audit_id']]['stageTimestamp'])) for o in ops if o['op'].startswith('patch') and o['rv']]
below = above = 0
for o in dels:
    e = ev[o['audit_id']]
    rrt = parse_k8s_time_ns(e['requestReceivedTimestamp'])
    prior = [rv for rv, t in com if t < rrt]
    if prior and int(o['rv']) < max(prior):
        below += 1
    else:
        above += 1
print('deletes whose returned RV < max RV of patches completed before the DELETE was received:', below, 'of', len(dels))
