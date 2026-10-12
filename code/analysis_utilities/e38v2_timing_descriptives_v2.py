"""POST-HOC descriptive timing for the Phase-2 race-timeline figure (Fig. 2), CONFIRMATORY rerun version.
Reads RAW_TRIALS.jsonl and webhook_log.jsonl of the E38v2 rerun (read-only) and writes
E38V2R_TIMING_DESCRIPTIVES.csv. Not part of the locked E38v2 analysis plan.
Additions vs e38v2_timing_descriptives.py: webhook call offsets relative to the client gate end
(A3; webhook calls joined to trials by Pod UID; wall clocks of runner and webhook on the same VM).
Usage: python e38v2_timing_descriptives_v2.py <dir with RAW_TRIALS.jsonl, webhook_log.jsonl> <out.csv>
"""
import json, statistics as st, sys, csv
from collections import defaultdict
RUN = sys.argv[1]
OUT = sys.argv[2]
rows = [json.loads(l) for l in open(RUN + '/RAW_TRIALS.jsonl')]
wh = [json.loads(l) for l in open(RUN + '/webhook_log.jsonl')]

def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float('nan')
    k = (len(xs) - 1) * p
    f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)

out = []
def add(metric, arm, cond, xs):
    if xs:
        out.append(dict(metric=metric, arm=arm, condition=cond, n=len(xs), median=round(st.median(xs), 3),
                        p05=round(q(xs, .05), 3), p95=round(q(xs, .95), 3)))

arms = sorted({r['arm'] for r in rows})
for arm in arms:
    rs = [r for r in rows if r['arm'] == arm and r.get('status') == 'COMPLETE']
    g = [(r['gate']['t_end_mono_ns'] - r['gate']['t_start_mono_ns']) / 1e6 for r in rs
         if r.get('gate') and r['gate'].get('t_end_mono_ns') and r['gate'].get('t_start_mono_ns')]
    add('gate_duration_ms', arm, 'all', g)
    ge = [(r['delete']['t_send_mono_ns'] - r['gate']['t_end_mono_ns']) / 1e6 for r in rs
          if r.get('delete') and r['delete'].get('t_send_mono_ns') and r.get('gate') and r['gate'].get('t_end_mono_ns')
          and r['condition'] != 'uid_rebind_control']
    add('gate_end_to_delete_send_ms', arm, 'all_dispatched', ge)
    for cond in ('no_write', 'async_scope', 'async_app_policy', 'async_evidence_last_literal'):
        d = [(r['delete']['t_resp_mono_ns'] - r['delete']['t_send_mono_ns']) / 1e6 for r in rs
             if r['condition'] == cond and r.get('delete') and r['delete'].get('t_resp_mono_ns')
             and r['delete'].get('t_send_mono_ns') and r['delete'].get('http_status') in (200, 202)]
        add('delete_rtt_accepted_ms', arm, cond, d)
    d = [(r['delete']['t_resp_mono_ns'] - r['gate']['t_end_mono_ns']) / 1e6 for r in rs
         if r['condition'] == 'no_write' and r.get('delete') and r['delete'].get('t_resp_mono_ns')
         and r['gate'].get('t_end_mono_ns') and r['delete'].get('http_status') in (200, 202)]
    add('gate_end_to_delete_response_ms', arm, 'no_write', d)
    w = [(r['writer']['t_resp_mono_ns'] - r['writer']['actual_release_mono_ns']) / 1e6 for r in rs
         if r.get('writer') and r['writer'].get('t_resp_mono_ns') and r['writer'].get('actual_release_mono_ns')]
    add('writer_patch_rtt_ms', arm, 'async_all', w)

ev = [(e['completed_wall_ns'] - e['received_wall_ns']) / 1e6 for e in wh
      if e.get('reads') and e.get('completed_wall_ns') and e.get('received_wall_ns')]
add('webhook_evaluation_ms(received->completed, evaluated calls)', 'A3_dcji_webhook', 'all', ev)

# webhook call offsets relative to the client gate end (wall clock), A3 dispatched trials
by_uid = defaultdict(list)
for e in wh:
    if e.get('pod_uid') and e.get('received_wall_ns'):
        by_uid[e['pod_uid']].append(e)
for cond_sel in ('no_write', 'async_all'):
    first_recv, first_ev_a, first_scope, first_app, first_done, second_recv, n_calls = [], [], [], [], [], [], []
    for r in rows:
        if r['arm'] != 'A3_dcji_webhook' or r.get('status') != 'COMPLETE':
            continue
        if cond_sel == 'no_write' and r['condition'] != 'no_write':
            continue
        if cond_sel == 'async_all' and not r['condition'].startswith('async_'):
            continue
        dl = r.get('delete') or {}
        if not dl.get('dispatched') or not r['gate'].get('t_end_wall_ns'):
            continue
        uid = dl.get('expected_uid')
        calls = sorted(by_uid.get(uid, []), key=lambda e: e['received_wall_ns'])
        if not calls:
            continue
        t0 = r['gate']['t_end_wall_ns']
        n_calls.append(len(calls))
        c1 = calls[0]
        first_recv.append((c1['received_wall_ns'] - t0) / 1e6)
        if c1.get('completed_wall_ns'):
            first_done.append((c1['completed_wall_ns'] - t0) / 1e6)
        rd = {x['object']: x for x in (c1.get('reads') or [])}
        if 'evidence-a' in rd:
            first_ev_a.append((rd['evidence-a']['t_recv_wall_ns'] - t0) / 1e6)
        if 'response-scope' in rd:
            first_scope.append((rd['response-scope']['t_recv_wall_ns'] - t0) / 1e6)
        if 'application-policy' in rd:
            first_app.append((rd['application-policy']['t_recv_wall_ns'] - t0) / 1e6)
        if len(calls) > 1:
            second_recv.append((calls[1]['received_wall_ns'] - t0) / 1e6)
    add('webhook_first_call_received_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, first_recv)
    add('webhook_first_call_evidence_a_read_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, first_ev_a)
    add('webhook_first_call_scope_read_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, first_scope)
    add('webhook_first_call_app_policy_read_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, first_app)
    add('webhook_first_call_completed_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, first_done)
    add('webhook_second_call_received_after_gate_end_ms', 'A3_dcji_webhook', cond_sel, second_recv)
    add('webhook_calls_per_dispatched_delete', 'A3_dcji_webhook', cond_sel, n_calls)

with open(OUT, 'w', newline='') as fh:
    w_ = csv.DictWriter(fh, fieldnames=['metric', 'arm', 'condition', 'n', 'median', 'p05', 'p95'])
    w_.writeheader(); w_.writerows(out)
for o in out:
    print(o)
