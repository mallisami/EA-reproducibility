#!/usr/bin/env python3
"""E38v2 RERUN descriptive smoke/run checks from RAW + oracle CSV (+ pod events). argv: run_dir oracle_csv
Quality gates printed at the end: INFRA_ERROR < 2%, oracle join >= 98%, primary vs corrected-secondary
agreement >= 95%, controls (no_write VALID, uid_rebind_control UID_BLOCKED)."""
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
join_rate = full / len(rows)
print(f'trials fully joined (all their audit events present): {full}/{len(rows)} = {join_rate:.4f}')
acc = [x for x in rows if x['delete_http_status'] in ('200', '202') and x['condition'].startswith('async')]
src = Counter(x['delete_rv_source'] for x in acc)
print('accepted async DELETEs: deletion-RV source', dict(src), '; by arm from pod watch',
      dict(Counter(x['arm'] for x in acc if x['delete_rv_source'] == 'pod_watch_deleted')))
pw_x = [x for x in rows if x['pod_watch_deleted_rv'] and x['delete_rv_source'] in ('audit+trial', 'audit', 'trial')]
pw_agree = sum(1 for x in pw_x if str(x['delete_rv']) == x['pod_watch_deleted_rv'])
print(f'pod-watch DELETED RV equals the pre-registered deletion RV: {pw_agree}/{len(pw_x)}')


def agree(col):
    both = [x for x in rows if x[col] in ('0', '1')]
    a = sum(x[col] == '1' for x in both)
    return a, len(both)


a2, n2 = agree('rules_agree')
a3_, n3 = agree('rules_agree_tertiary')
print(f'rules agree primary vs corrected secondary (v1.2): {a2}/{n2} = {a2 / max(1, n2):.4f}')
print(f'rules agree primary vs tertiary (v1 rule, DELETE received): {a3_}/{n3} = {a3_ / max(1, n3):.4f}')
print('primary classes', dict(Counter(x['primary_class'] for x in rows)))
print('secondary classes', dict(Counter(x['secondary_class'] for x in rows)))
print('tertiary classes', dict(Counter(x['tertiary_class'] for x in rows)))
print('UNKNOWN reasons', dict(Counter(x['primary_reason'] for x in rows if x['primary_class'] == 'UNKNOWN')))
print('disagreements primary vs secondary (both classified):')
for x in rows:
    if x['rules_agree'] == '0':
        print('   ', x['trial_id'], x['arm'], x['condition'], x['delta_ms'], x['primary_class'], x['secondary_class'],
              'rv_margin', x['rv_margin'], 'time_margin_us', x['time_margin_us'], x['delete_rv_source'])
tab = defaultdict(Counter)
for x in rows:
    tab[(x['arm'], x['condition'])][x['primary_class']] += 1
for k in sorted(tab):
    print('  ', k[0], k[1], dict(tab[k]))
inv = defaultdict(list)
for x in rows:
    if x['condition'].startswith('async'):
        inv[(x['arm'], x['condition'])].append((float(x['delta_ms']), x['primary_class'][:4]))
for k in sorted(inv):
    print('  ', k[0][:6], k[1][:12], ' '.join('%g:%s' % d for d in sorted(inv[k])))
rel = defaultdict(list)
for r in raw:
    w = r['writer']
    if w and w.get('actual_release_mono_ns'):
        rel['neg' if r['delta_ms'] < 0 else 'nonneg'].append((w['actual_release_mono_ns'] - w['intended_release_mono_ns']) / 1e3)
for k, v in rel.items():
    v.sort()
    print(f'writer release error us [{k}]: n={len(v)} median={st.median(v):.1f} p95={v[int(.95*len(v))-1]:.1f} max={v[-1]:.1f} frac>100us={sum(abs(x)>100 for x in v)/len(v):.3f} frac>500us={sum(abs(x)>500 for x in v)/len(v):.3f}')
lat, rtt = defaultdict(list), defaultdict(list)
for r in raw:
    d, g = r['delete'], r['gate']
    if d['dispatched'] and g and g.get('t_end_mono_ns'):
        lat['writer' if r['writer'] else 'no_writer'].append((d['t_send_mono_ns'] - g['t_end_mono_ns']) / 1e3)
        if d['http_status'] in (200, 202):
            rtt[r['arm']].append((d['t_resp_mono_ns'] - d['t_send_mono_ns']) / 1e6)
for k, v in lat.items():
    v.sort()
    print(f'gate-end -> DELETE send us [{k}]: n={len(v)} median={st.median(v):.1f} p95={v[int(.95*len(v))-1]:.1f} max={v[-1]:.1f}')
for k, v in sorted(rtt.items()):
    v.sort()
    print(f'accepted DELETE RTT ms [{k}]: n={len(v)} median={st.median(v):.2f} p95={v[int(.95*len(v))-1]:.2f}')
# ---- running target Pod
ready = [r['fixture']['pod_ready']['wait_ms'] for r in raw if r.get('fixture') and r['fixture'].get('pod_ready')]
gs = [r['gate']['pod_state'] for r in raw if r.get('gate') and r['gate'].get('pod_state')]
rr = sum(1 for s in gs if s['phase'] == 'Running' and s['ready'] is True and not s['terminating'])
print(f'running pod: ready wait ms n={len(ready)} median={st.median(ready) if ready else None} max={max(ready) if ready else None}; '
      f'gate saw Running+Ready {rr}/{len(gs)} (of {sum(1 for r in raw if r.get("gate"))} gated trials)')
gone = [(r['effect']['t_gone_mono_ns'] - r['delete']['t_resp_mono_ns']) / 1e6 for r in raw
        if r['delete']['http_status'] in (200, 202) and r['effect'].get('t_gone_mono_ns')]
ng = sum(1 for r in raw if r['delete']['http_status'] in (200, 202) and not r['effect'].get('t_gone_mono_ns'))
print(f'pod object gone after accepted DELETE response: n={len(gone)} (not gone: {ng}); '
      f'median {st.median(gone) if gone else None} ms, max {max(gone) if gone else None} ms')
gl = [(r['gate']['t_end_mono_ns'] - r['gate']['t_start_mono_ns']) / 1e6 for r in raw if r['gate']]
print('gate duration ms median %.3f' % st.median(gl))
# ---- quality gates
ctrl_nw = [x['primary_class'] for x in rows if x['condition'] == 'no_write']
ctrl_uid = [x['primary_class'] for x in rows if x['condition'] == 'uid_rebind_control']
print('QUALITY GATES:',
      'INFRA<2%', 'PASS' if infra / n < 0.02 else 'FAIL', '|',
      'join>=98%', 'PASS' if join_rate >= 0.98 else 'FAIL', '|',
      'agree(primary,secondary)>=95%', 'PASS' if n2 and a2 / n2 >= 0.95 else 'FAIL', '|',
      'no_write all VALID', 'PASS' if ctrl_nw and all(c == 'VALID_EXECUTED' for c in ctrl_nw) else 'CHECK', '|',
      'uid all UID_BLOCKED', 'PASS' if ctrl_uid and all(c == 'UID_BLOCKED' for c in ctrl_uid) else 'CHECK')
