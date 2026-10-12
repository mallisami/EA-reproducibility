#!/usr/bin/env python3
"""Descriptive smoke/run checks from RAW + oracle CSV. argv: run_dir oracle_csv"""
import csv, json, statistics as st, sys
from collections import Counter, defaultdict
rd, oc = sys.argv[1], sys.argv[2]
raw = [json.loads(l) for l in open(rd + '/RAW_TRIALS.jsonl') if l.strip()]
rows = list(csv.DictReader(open(oc)))
n = len(raw)
print('trials', n, 'status', dict(Counter(r['status'] for r in raw)))
infra = sum(r['status'] == 'INFRA_ERROR' for r in raw)
print('INFRA_ERROR rate %.4f' % (infra / n))
for r in raw:
    if r['status'] != 'COMPLETE':
        print('  non-complete', r['trial_id'], r['arm'], r['condition'], r['delta_ms'], r['status'], (r['error'] or '')[:200])
# oracle joins
disp = [x for x in rows if x['delete_dispatched'] == 'True']
dj = sum(1 for x in disp if x['delete_audit_present'] == '1')
wr = [x for x in rows if x['writer_object']]
wj = sum(1 for x in wr if x['writer_audit_present'] == '1')
a3 = [x for x in disp if x['arm'] == 'A3_dcji_webhook' and x['condition'] != 'uid_rebind_control']
a3j = sum(1 for x in a3 if x['webhook_request_uid'])
non = [x for x in disp if x['arm'] != 'A3_dcji_webhook' and x['webhook_request_uid']]
print(f'audit join: DELETE {dj}/{len(disp)}, writer PATCH {wj}/{len(wr)}; webhook join A3 {a3j}/{len(a3)}; non-A3 with webhook entry {len(non)}')
full = sum(1 for x in rows if (x['delete_dispatched'] != 'True' or x['delete_audit_present'] == '1')
           and (not x['writer_object'] or x['writer_audit_present'] == '1'))
print(f'trials fully joined (all their audit events present): {full}/{len(rows)} = {full / len(rows):.4f}')
both = [x for x in rows if x['rules_agree'] in ('0', '1')]
print(f'rules agree: {sum(x["rules_agree"] == "1" for x in both)}/{len(both)}' +
      (f' = {sum(x["rules_agree"] == "1" for x in both) / len(both):.4f}' if both else ''))
print('primary classes', dict(Counter(x['primary_class'] for x in rows)))
print('secondary classes', dict(Counter(x['secondary_class'] for x in rows)))
print('UNKNOWN reasons', dict(Counter(x['primary_reason'] for x in rows if x['primary_class'] == 'UNKNOWN')))
print('disagreements (primary!=secondary, both classified):')
for x in both:
    if x['rules_agree'] == '0':
        print('   ', x['trial_id'], x['arm'], x['condition'], x['delta_ms'], x['primary_class'], x['secondary_class'],
              'rv_margin', x['rv_margin'], 'time_margin_us', x['time_margin_us'])
# per arm x condition outcome table
tab = defaultdict(Counter)
for x in rows:
    tab[(x['arm'], x['condition'])][x['primary_class']] += 1
for k in sorted(tab):
    print('  ', k[0], k[1], dict(tab[k]))
# invalid executed by delta per arm/cond (smoke: 1 rep)
inv = defaultdict(list)
for x in rows:
    if x['condition'].startswith('async'):
        inv[(x['arm'], x['condition'])].append((float(x['delta_ms']), x['primary_class'][:4]))
for k in sorted(inv):
    print('  ', k[0][:6], k[1][:12], ' '.join('%g:%s' % d for d in sorted(inv[k])))
# timing
dur = []
recs = sorted(raw, key=lambda r: r['trial_id'])
rel = defaultdict(list)
for r in raw:
    w = r['writer']
    if w and w.get('actual_release_mono_ns'):
        rel['neg' if r['delta_ms'] < 0 else 'nonneg'].append((w['actual_release_mono_ns'] - w['intended_release_mono_ns']) / 1e3)
for k, v in rel.items():
    v.sort()
    print(f'writer release error us [{k}]: n={len(v)} median={st.median(v):.1f} p05={v[int(.05*len(v))]:.1f} p95={v[int(.95*len(v))-1]:.1f} max={v[-1]:.1f} frac>100us={sum(abs(x)>100 for x in v)/len(v):.3f} frac>500us={sum(abs(x)>500 for x in v)/len(v):.3f}')
# DELETE send latency after gate end: writer trials vs no-write (GIL / contention check)
lat = defaultdict(list)
rtt = defaultdict(list)
for r in raw:
    d, g = r['delete'], r['gate']
    if d['dispatched'] and g and g.get('t_end_mono_ns'):
        key = 'writer' if r['writer'] else 'no_writer'
        lat[key].append((d['t_send_mono_ns'] - g['t_end_mono_ns']) / 1e3)
        if d['http_status'] in (200, 202):
            rtt[r['arm']].append((d['t_resp_mono_ns'] - d['t_send_mono_ns']) / 1e3)
for k, v in lat.items():
    v.sort()
    print(f'gate-end -> DELETE send us [{k}]: n={len(v)} median={st.median(v):.1f} p95={v[int(.95*len(v))-1]:.1f} max={v[-1]:.1f}')
for k, v in sorted(rtt.items()):
    v.sort()
    print(f'accepted DELETE RTT us [{k}]: n={len(v)} median={st.median(v):.1f} p95={v[int(.95*len(v))-1]:.1f}')
gl = [ (r['gate']['t_end_mono_ns'] - r['gate']['t_start_mono_ns']) / 1e6 for r in raw if r['gate']]
print('gate duration ms median %.3f' % st.median(gl))
rd_ = [float(x['realized_delta_ms']) for x in rows if x['realized_delta_ms']]
