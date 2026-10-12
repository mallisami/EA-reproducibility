#!/usr/bin/env python3
"""E38v2 RERUN descriptive report on the Running target Pod (not part of the pre-registered analysis).
argv: RUN_DIR ORACLE_CSV POD_EVENTS OUT_JSON
 - readiness wait before the gate; Pod state seen by the gate's Pod read
 - DELETE accepted -> Pod object gone (client GET), per arm
 - deletion-RV source per arm (audit+trial vs pod-watch fallback = kubelet-removal race)
 - pod-watch: RV/time of the commit that set deletionTimestamp ("deletion accepted") vs the removal (DELETED)
 - descriptive alternative reference: write committed before the deletionTimestamp commit (instead of removal)"""
import csv, json, statistics as st, sys
from collections import Counter, defaultdict

rd, oc, pe_p, out = sys.argv[1:5]
raw = {r['trial_id']: r for r in (json.loads(l) for l in open(rd + '/RAW_TRIALS.jsonl') if l.strip())}
rows = {int(r['trial_id']): r for r in csv.DictReader(open(oc))}
ev = defaultdict(list)
for l in open(pe_p):
    if l.strip():
        e = json.loads(l)
        if e.get('uid'):
            ev[e['uid']].append(e)


def q(v):
    v = sorted(v)
    if not v:
        return None
    return {'n': len(v), 'median': round(st.median(v), 3), 'p95': round(v[max(0, int(.95 * len(v)) - 1)], 3),
            'max': round(v[-1], 3), 'min': round(v[0], 3)}


res = {}
ready = [r['fixture']['pod_ready']['wait_ms'] for r in raw.values() if r.get('fixture') and r['fixture'].get('pod_ready')]
res['ready_wait_ms'] = q(ready)
gs = [r['gate']['pod_state'] for r in raw.values() if r.get('gate') and r['gate'].get('pod_state')]
res['gate_pod_state'] = {'n_gated': sum(1 for r in raw.values() if r.get('gate')), 'n_with_state': len(gs),
                         'running_ready_not_terminating': sum(1 for s in gs if s['phase'] == 'Running' and s['ready'] and not s['terminating']),
                         'container_running': sum(1 for s in gs if s['container_state'] == 'running')}
per_arm = defaultdict(lambda: defaultdict(list))
alt = defaultdict(Counter)
for tid, r in raw.items():
    o = rows.get(tid)
    d = r['delete']
    if not o or d['http_status'] not in (200, 202):
        continue
    arm = r['arm']
    pa = per_arm[arm]
    if r['effect'].get('t_gone_mono_ns'):
        pa['gone_after_resp_ms'].append((r['effect']['t_gone_mono_ns'] - d['t_resp_mono_ns']) / 1e6)
    pa['rtt_ms'].append((d['t_resp_mono_ns'] - d['t_send_mono_ns']) / 1e6)
    pa['src'].append(o['delete_rv_source'] or '(none)')
    evs = ev.get(d['expected_uid'], [])
    acc = next((e for e in evs if e['type'] in ('MODIFIED', 'ADDED', 'LIST') and e.get('deletion_timestamp')), None)
    dele = [e for e in evs if e['type'] == 'DELETED']
    if acc and len(dele) == 1:
        pa['accepted_to_deleted_rv_gap'].append(int(dele[0]['rv']) - int(acc['rv']))
        pa['accepted_to_deleted_recv_ms'].append((dele[0]['t_recv_wall_ns'] - acc['t_recv_wall_ns']) / 1e6)
        pa['deleted_event_container_state'].append(str(dele[0].get('container_state')))
    # descriptive alternative reference (deletionTimestamp commit) for async trials with a writer
    if r['condition'].startswith('async') and o['writer_rv'] != '' and acc:
        key = (arm, r['condition'], r['delta_ms'])
        inv_state = o['v_post'] == '0'
        alt[key]['n_accepted'] += 1
        alt[key]['primary_invalid'] += o['primary_class'] == 'INVALID_EXECUTED'
        alt[key]['write_before_deletionTimestamp_commit_and_invalid'] += (int(o['writer_rv']) < int(acc['rv'])) and inv_state
res['per_arm'] = {}
for arm, pa in sorted(per_arm.items()):
    res['per_arm'][arm] = {'n_accepted': len(pa['rtt_ms']), 'rtt_ms': q(pa['rtt_ms']),
                           'gone_after_response_ms': q(pa['gone_after_resp_ms']),
                           'deletion_rv_source': dict(Counter(pa['src'])),
                           'pod_watch_accepted_to_deleted_rv_gap': q(pa['accepted_to_deleted_rv_gap']),
                           'pod_watch_accepted_to_deleted_recv_ms': q(pa['accepted_to_deleted_recv_ms']),
                           'container_state_in_DELETED_event': dict(Counter(pa['deleted_event_container_state']))}
res['alt_reference_deletionTimestamp_commit'] = [
    dict(arm=k[0], condition=k[1], delta_ms=k[2], **v) for k, v in sorted(alt.items(), key=lambda x: (x[0][0], x[0][1], x[0][2]))]
json.dump(res, open(out, 'w'), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != 'alt_reference_deletionTimestamp_commit'}, indent=1))
tot = Counter()
for a in res['alt_reference_deletionTimestamp_commit']:
    tot[(a['arm'], 'primary')] += a['primary_invalid']
    tot[(a['arm'], 'alt')] += a['write_before_deletionTimestamp_commit_and_invalid']
print('INVALID (accepted async) primary vs alt-reference(deletionTimestamp commit) by arm:',
      {arm: (tot[(arm, 'primary')], tot[(arm, 'alt')]) for arm in sorted({k[0] for k in tot})})
