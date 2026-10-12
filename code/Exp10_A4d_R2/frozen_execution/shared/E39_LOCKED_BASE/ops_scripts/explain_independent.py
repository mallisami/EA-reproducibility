#!/usr/bin/env python3
"""Per-trial explanation of the differences between the independent recomputation and the oracle (reads only).
Usage: explain_independent.py E RUN_DIR OUT_TXT"""
import collections, csv, json, sys

E, R, OUT = sys.argv[1:4]
sys.path.insert(0, E + '/analysis')
import recompute_independent_e39 as ind  # noqa: E402

raw = [json.loads(l) for l in open(R + '/RAW_TRIALS.jsonl') if l.strip()]
audit = []
for l in open(R + '/audit.log'):
    if '"ResponseComplete"' in l:
        audit.append(json.loads(l))
aud = ind.audit_rc(audit)
orc = {int(r['trial_id']): r for r in csv.DictReader(open(R + '/analysis_final/oracle_trials_E39.csv'))}
diff = collections.Counter()
examples = collections.defaultdict(list)
for t in raw:
    if str(t.get('experiment')).upper() not in ind.HEADLINE:
        continue
    kind, reason = ind.classify(t, aud)
    o = orc[t['trial_id']]
    pc = o['primary_class']
    main_in = pc in ('VALID_EXECUTED', 'INVALID_EXECUTED', 'CORRECT_ABORT', 'FALSE_ABORT', 'UID_BLOCKED') or \
        (pc == 'UNKNOWN' and 'gate_unknown_coherence' in o['primary_reason'])
    main_ev = pc == 'INVALID_EXECUTED'
    ind_in = kind != 'excluded'
    ind_ev = kind == 'event'
    if (main_in, main_ev) != (ind_in, ind_ev):
        key = (t['experiment'], o['arm_c'], 'main=%s' % (pc if main_in else 'excluded:' + o['primary_reason']),
               'independent=%s:%s' % (kind, reason), 'action_rv_source=%s' % o['action_rv_source'])
        diff[key] += 1
        if len(examples[key]) < 3:
            examples[key].append(t['trial_id'])
lines = ['Per-trial differences independent recomputation vs oracle primary (trial ids: first 3 examples)']
for k, v in sorted(diff.items(), key=lambda kv: -kv[1]):
    lines.append('%5d  %s  e.g. %s' % (v, ' | '.join(k), examples[k]))
lines.append('total differing trials: %d' % sum(diff.values()))
open(OUT, 'w').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
