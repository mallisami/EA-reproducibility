#!/usr/bin/env python3
"""E39 operational quality gates on an oracle CSV (smoke or stage). Operator tool; reports only.
Usage: quality_gates.py E RUN_DIR ORACLE_CSV OUT_JSON"""
import csv, json, statistics, sys, collections

E, R, CSV, OUT = sys.argv[1:5]
sys.path.insert(0, E + '/src')
from schema_e39 import validate_record_e39  # noqa: E402

raw = [json.loads(l) for l in open(R + '/RAW_TRIALS.jsonl') if l.strip()]
bad_schema = []
for r in raw:
    try:
        validate_record_e39(r)
    except Exception as exc:
        bad_schema.append((r['trial_id'], repr(exc)[:200]))
rows = list(csv.DictReader(open(CSV)))
byid = {int(r['trial_id']): r for r in raw}
n = len(rows)
g = {}
infra = [r for r in rows if r['status'] == 'INFRA_ERROR' or r['primary_class'] == 'INFRA_ERROR']
g['n_trials'] = n
g['infra_error'] = len(infra)
g['infra_rate'] = len(infra) / n if n else None
g['infra_examples'] = [(r['trial_id'], r['experiment'], r['arm'], (byid.get(int(r['trial_id'])) or {}).get('error'))
                       for r in infra[:8]]
g['primary_classes'] = dict(collections.Counter(r['primary_class'] for r in rows))
g['primary_by_exp'] = {x: dict(collections.Counter(r['primary_class'] for r in rows if r['experiment'] == x))
                       for x in sorted({r['experiment'] for r in rows})}
unk = collections.Counter((r['experiment'], r['primary_reason']) for r in rows if r['primary_class'] == 'UNKNOWN')
g['unknown_reasons'] = {'%s|%s' % k: v for k, v in unk.items()}
# join: every write / arrival / cut / mirror / action audit id of the trial has exactly one ResponseComplete
# event in the audit log; A3 actions that reached admission (not a 409 precondition failure) joined to a webhook entry
rc_count = collections.Counter()
for l in open(R + '/audit.log'):
    if '"ResponseComplete"' in l:
        e = json.loads(l)
        if e.get('stage') == 'ResponseComplete':
            rc_count[e['auditID']] += 1
joinable = [r for r in rows if r['experiment'] != 'C' and r['primary_class'] != 'INFRA_ERROR']
nj = 0
miss = []
n_ids = n_ids_ok = 0
for r in joinable:
    t = byid[int(r['trial_id'])]
    ids = []
    if isinstance(t.get('writer'), dict) and t['writer'].get('http_status') is not None:
        ids.append(('writer', t['writer'].get('audit_id')))
    for w in t.get('writer_arrivals') or []:
        if w.get('http_status') is not None:
            ids.append(('arrival', w.get('audit_id')))
    for w in t.get('cut_writes') or []:
        ids.append(('cut', w.get('audit_id')))
    if isinstance(t.get('mirror'), dict) and t['mirror'].get('mirror_commit_rv'):
        ids.append(('mirror', t['mirror'].get('mirror_audit_id')))
    if (t.get('delete') or {}).get('dispatched'):
        ids.append(('delete', t['delete'].get('audit_id')))
    if isinstance(t.get('netpol'), dict) and t['netpol'].get('http_status') is not None:
        ids.append(('netpol', t['netpol'].get('audit_id')))
    bad = [k for k, a in ids if not a or rc_count.get(a) != 1]
    n_ids += len(ids)
    n_ids_ok += len(ids) - len(bad)
    if r['arm_c'] == 'A3' and r['action_dispatched'] == 'True' and r['action_http_status'] != '409' \
            and not r['webhook_decision']:
        bad.append('webhook')
    nj += not bad
    if bad:
        miss.append((r['trial_id'], r['experiment'], r['arm'], bad))
g['audit_ids_joined'] = '%d/%d' % (n_ids_ok, n_ids)
g['join_rate'] = nj / len(joinable) if joinable else None
g['join_misses'] = miss[:10]
both = [r for r in rows if r['primary_class'] and r['secondary_class'] and r['experiment'] != 'C'
        and r['primary_class'] not in ('INFRA_ERROR',)]
g['agreement_primary_secondary'] = (sum(1 for r in both if r['primary_class'] == r['secondary_class']) / len(both)) if both else None
g['disagreements'] = [(r['trial_id'], r['experiment'], r['arm'], r['delta_ms'], r['primary_class'], r['secondary_class'])
                      for r in both if r['primary_class'] != r['secondary_class']][:12]
ctrl = collections.Counter()
for r in rows:
    if r['condition'] == 'no_write':
        ctrl[('no_write', r['arm'], r['primary_class'])] += 1
    if r['condition'] == 'uid_rebind_control':
        ctrl[('uid_rebind', r['arm'], r['primary_class'])] += 1
    if r['experiment'] == 'G':
        ctrl[('G_' + r['condition'], r['arm'], r['primary_class'])] += 1
g['controls'] = {'|'.join(k): v for k, v in sorted(ctrl.items())}
g['controls_ok'] = (all(c == 'VALID_EXECUTED' for (k, a, c) in ctrl if k == 'no_write')
                    and all(c == 'UID_BLOCKED' for (k, a, c) in ctrl if k == 'uid_rebind')
                    and all(c == 'CORRECT_ABORT' for (k, a, c) in ctrl if k == 'G_in_model_ace'))
g['conflict_kinds'] = dict(collections.Counter(r['conflict_kind'] for r in rows if r['action_http_status'] == '409'))
g['flag_unparsed'] = sum(1 for r in rows if 'unparsed' in (r['flags'] or ''))
g['c_fields_ok'] = all((byid[int(r['trial_id'])].get('acquisition') or {}).get('refresh_ms') is not None
                       and (byid[int(r['trial_id'])].get('acquisition') or {}).get('gate_ms') is not None
                       for r in rows if r['experiment'] == 'C')
g['d_fields_ok'] = all(all(k in (byid[int(r['trial_id'])].get('host') or {}) for k in ('cpu_pct', 'free_ram_mb', 'load_throttled'))
                       for r in rows if r['experiment'] == 'D')
g['schema_invalid'] = bad_schema[:10]
g['n_schema_invalid'] = len(bad_schema)
# per-experiment wall time per trial from runner monotonic stamps are not in the CSV; use RAW order + wall
dur = collections.defaultdict(list)
prev = None
for r in raw:
    t = (r.get('fixture') or {}).get('t_reset_start_wall_ns') or None
    dur[r['experiment']].append(r)
g['n_by_exp'] = {k: len(v) for k, v in dur.items()}
g['GATES'] = {'infra_lt_2pct': (g['infra_rate'] or 0) < 0.02, 'join_ge_98pct': (g['join_rate'] or 0) >= 0.98,
              'agreement_ge_95pct': (g['agreement_primary_secondary'] or 0) >= 0.95, 'controls_ok': g['controls_ok'],
              'schema_valid': not bad_schema, 'no_unparsed_409': g['flag_unparsed'] == 0,
              'c_fields': g['c_fields_ok'], 'd_fields': g['d_fields_ok']}
g['ALL_GATES_PASS'] = all(g['GATES'].values())
json.dump(g, open(OUT, 'w'), indent=1, default=str)
print(json.dumps(g, indent=1, default=str))
