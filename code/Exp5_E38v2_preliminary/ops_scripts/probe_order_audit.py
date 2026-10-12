import json, sys
sys.path.insert(0, sys.argv[2] + '/analysis')
from e38v2_common import parse_k8s_time_ns
ops = json.load(open(sys.argv[1]))
ev = {}
for l in sys.stdin:
    if l.strip():
        e = json.loads(l)
        if e['stage'] == 'ResponseComplete':
            ev[e['auditID']] = e
seq, miss = [], 0
for o in ops:
    e = ev.get(o['audit_id'])
    if e is None:
        miss += 1
        continue
    if o['status'] == 200 and o['rv']:
        seq.append((int(o['rv']), parse_k8s_time_ns(e['stageTimestamp']), parse_k8s_time_ns(e['requestReceivedTimestamp']), o['op']))
rvs = [s[0] for s in seq]
print('ops', len(ops), 'audit-joined', len(ops) - miss, 'committed', len(seq), 'distinct RVs', len(set(rvs)))
seq.sort()


def pairwise(xs):
    c = d = t = 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if xs[i][1] == xs[j][1]:
                t += 1
            elif xs[i][1] < xs[j][1]:
                c += 1
            else:
                d += 1
    return c, d, t


for name, sub in (('all', seq), ('sequential', [s for s in seq if s[3].startswith('seq')]),
                  ('concurrent', [s for s in seq if s[3].startswith('conc')])):
    c, d, t = pairwise(sub)
    adj = sum(1 for a, b in zip(sub, sub[1:]) if a[1] < b[1])
    # the oracle's secondary rule compares writer ResponseComplete with DELETE requestReceived:
    # count (patch, delete) pairs where RV order and (patch stageTimestamp < delete received) disagree
    pd = [(a, b) for a in sub for b in sub if a[3].endswith(('patch_writer', 'patch_reset', 'seq_patch')) and 'delete' in b[3]]
    dis = sum(1 for a, b in pd if (a[0] < b[0]) != (a[1] < b[2]))
    print(f'{name}: n={len(sub)} pairwise RV-vs-stageTimestamp concordant={c} discordant={d} ties={t} '
          f'(agreement {c / max(1, c + d):.5f}); adjacent concordant {adj}/{max(0, len(sub) - 1)}; '
          f'patch-vs-delete primary/secondary rule disagreements {dis}/{len(pd)}')
