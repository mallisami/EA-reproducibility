import json, sys, statistics as st
from collections import defaultdict
sys.path.insert(0, sys.argv[2] + '/analysis')
from e38v2_common import parse_k8s_time_ns
rd = sys.argv[1]
raw = [json.loads(l) for l in open(rd + '/RAW_TRIALS.jsonl')]
wh = [json.loads(l) for l in open(rd + '/webhook_log.jsonl')]
au = {}
for l in open(rd + '/audit.log'):
    e = json.loads(l)
    au[e['auditID']] = e
bypod = defaultdict(list)
for w in wh:
    bypod[w['pod_uid']].append(w)
# webhook timing breakdown
tot, rds, gaps = [], [], []
for w in wh:
    if w['pod_name'] != 'e38v2-target' or not w['reads']:
        continue
    tot.append((w['completed_wall_ns'] - w['received_wall_ns']) / 1e6)
    for r in w['reads']:
        rds.append((r['t_recv_wall_ns'] - r['t_send_wall_ns']) / 1e6)
    gaps.append((w['reads'][0]['t_send_wall_ns'] - w['received_wall_ns']) / 1e6)
print('webhook eval ms: median %.2f max %.2f; per-read ms median %.3f p95 %.3f; received->first read ms median %.3f'
      % (st.median(tot), max(tot), st.median(rds), sorted(rds)[int(.95 * len(rds))], st.median(gaps)))
# A3 accepted DELETE: breakdown DELETE received (audit) -> webhook received -> webhook done -> DELETE complete
for r in raw:
    if r['arm'] != 'A3_dcji_webhook' or not r['delete']['dispatched']:
        continue
    e = au.get(r['delete']['audit_id'])
    ws = bypod.get(r['delete']['expected_uid'], [])
    if not e:
        continue
    rr, sc = parse_k8s_time_ns(e['requestReceivedTimestamp']), parse_k8s_time_ns(e['stageTimestamp'])
    parts = ['trial %d d=%s code=%s rtt=%.1fms audit(recv->done)=%.1fms' % (r['trial_id'], r['delta_ms'], r['delete']['http_status'],
             (r['delete']['t_resp_mono_ns'] - r['delete']['t_send_mono_ns']) / 1e6, (sc - rr) / 1e6)]
    for w in ws:
        parts.append('wh[%s%s rv=%s recv+%.1f eval %.1f]' % (w['decision'], '(term)' if w.get('pod_terminating') else '',
                     w.get('pod_resource_version'), (w['received_wall_ns'] - rr) / 1e6, (w['completed_wall_ns'] - w['received_wall_ns']) / 1e6))
    print(' '.join(parts))
# writer PATCH rtt
pr = [(r['writer']['t_resp_mono_ns'] - r['writer']['actual_release_mono_ns']) / 1e6 for r in raw if r['writer']]
print('writer PATCH rtt ms median %.2f p95 %.2f' % (st.median(pr), sorted(pr)[int(.95 * len(pr))]))
dr = [(r['delete']['t_resp_mono_ns'] - r['delete']['t_send_mono_ns']) / 1e6 for r in raw if r['delete']['dispatched'] and r['arm'] != 'A3_dcji_webhook']
print('non-A3 DELETE rtt ms median %.2f p95 %.2f' % (st.median(dr), sorted(dr)[int(.95 * len(dr))]))
nw = [(r['delete']['t_send_mono_ns'] - r['gate']['t_end_mono_ns']) / 1e3 for r in raw if r['condition'] == 'no_write']
print('no_write gate-end->DELETE send us', nw)
