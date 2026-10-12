#!/usr/bin/env python3
"""E38v2 RERUN: map webhook evaluations to kube-apiserver log lines, per Pod DELETE request.

Evidence used (kube-apiserver --v=3 --vmodule=store=6,round_trippers=6, container log):
  HTTP   httplog.go   "HTTP" verb="DELETE" URI=/api/v1/namespaces/<ns>/pods/<name> latency=.. audit-ID=.. resp=..
         (one line per request, logged at completion; start = end - latency)
  WH     round_trippers.go "Response" verb="POST" url="https://e38v2-webhook.e38v2-system.svc:443/validate..."
         (one line per admission-webhook call made by the apiserver, logged when the webhook response arrives)
  GTD    store.go:1277 "Going to delete object from registry" object="<ns>/<name>"      (registry Delete, just
         before the storage-layer delete)
  CONF   store.go:452  "deletion of /registry/pods/<ns>/<name> failed because of a conflict, going to retry"
  TBU    store.go:660  "Going to delete object from registry, triggered by update" object="<ns>/<name>"
The webhook's own log (webhook_log.jsonl) gives, per call, decision, pod_terminating, pod_resource_version and
received/completed wall times (same host clock). Calls are matched to WH lines in time order within the
DELETE request's window. Nothing here is inferred beyond the order of logged events.

argv: RUN_DIR APISERVER_LOG ORACLE_CSV OUT_JSON [namespace]
"""
import csv, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

rd, api_p, oc_p, out_p = sys.argv[1:5]
raw = [json.loads(l) for l in open(rd + '/RAW_TRIALS.jsonl') if l.strip()]
run_id = raw[0]['run_id'] if raw else None
NS = sys.argv[5] if len(sys.argv) > 5 else 'e38v2-' + str(run_id)
wh = [json.loads(l) for l in open(rd + '/webhook_log.jsonl') if l.strip()]
orow = {r['trial_id']: r for r in csv.DictReader(open(oc_p))}

KLOG = re.compile(r'^(\S+) (?:stdout|stderr) \S ([IWEF])(\d{4}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})\s+\d+ (\S+?):(\d+)\] (.*)$')
LAT = re.compile(r'latency="([0-9.]+)(ns|µs|us|ms|s)"')


def lat_ns(s):
    m = LAT.search(s)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return int(v * {'ns': 1, 'µs': 1e3, 'us': 1e3, 'ms': 1e6, 's': 1e9}[u])


def field(s, k):
    m = re.search(k + r'="([^"]*)"', s)
    return m.group(1) if m else None


reqs, events = [], []   # reqs: DELETE requests on pods in NS; events: (t_ns, kind, obj, extra)
n_lines = 0
with open(api_p, errors='replace') as fh:
    for line in fh:
        n_lines += 1
        if ('httplog.go' not in line and 'round_trippers.go' not in line and 'store.go' not in line):
            continue
        m = KLOG.match(line.rstrip('\n'))
        if not m:
            continue
        cri, lvl, mmdd, hh, mi, ss, us, src, lno, body = m.groups()
        year = int(cri[:4])
        t = int(datetime(year, int(mmdd[:2]), int(mmdd[2:]), int(hh), int(mi), int(ss),
                         tzinfo=timezone.utc).timestamp()) * 10**9 + int(us) * 1000
        if src == 'httplog.go' and '"HTTP"' in body and 'verb="DELETE"' in body:
            uri = field(body, 'URI') or ''
            pre = '/api/v1/namespaces/%s/pods/' % NS
            if uri.startswith(pre):
                L = lat_ns(body)
                reqs.append({'t_end': t, 't_start': t - (L or 0), 'latency_ms': None if L is None else L / 1e6,
                             'name': uri[len(pre):].split('?')[0], 'audit_id': field(body, 'audit-ID'),
                             'user_agent': field(body, 'userAgent'),
                             'resp': (re.search(r'resp=(\d+)', body) or [None, None])[1]})
        elif src == 'round_trippers.go' and '"Response"' in body and 'e38v2-webhook.e38v2-system.svc' in body:
            events.append((t, 'WH', None, {'ms': field(body, 'milliseconds') or (re.search(r'milliseconds=(\d+)', body) or [0, None])[1],
                                           'status': field(body, 'status')}))
        elif src == 'store.go' and 'Going to delete object from registry, triggered by update' in body:
            events.append((t, 'TBU', (field(body, 'object') or '').split('/')[-1], {'line': src + ':' + lno}))
        elif src == 'store.go' and 'Going to delete object from registry' in body:
            events.append((t, 'GTD', (field(body, 'object') or '').split('/')[-1], {'line': src + ':' + lno}))
        elif src == 'store.go' and 'failed because of a conflict, going to retry' in body and ('/registry/pods/%s/' % NS) in body:
            nm = body.split('/registry/pods/%s/' % NS, 1)[1].split(' ', 1)[0]
            events.append((t, 'CONF', nm, {'line': src + ':' + lno}))
events.sort(key=lambda e: e[0])
trial_by_aid = {r['delete']['audit_id']: r for r in raw if r['delete'].get('audit_id')}
wh_by_name = defaultdict(list)
for w in wh:
    wh_by_name[w.get('pod_name')].append(w)
for v in wh_by_name.values():
    v.sort(key=lambda w: w['completed_wall_ns'])

import bisect
SLACK = 2_000_000
ev_t = [e[0] for e in events]
recs = []
for q in reqs:
    lo, hi = q['t_start'] - SLACK, q['t_end'] + SLACK
    win = events[bisect.bisect_left(ev_t, lo):bisect.bisect_right(ev_t, hi)]
    evs = [e for e in win if (e[1] == 'WH' or e[2] == q['name'])]
    wl = wh_by_name.get(q['name'], [])
    wt = [w['completed_wall_ns'] for w in wl]
    calls = wl[bisect.bisect_left(wt, lo):bisect.bisect_right(wt, hi)]
    whl = [e for e in evs if e[1] == 'WH']
    matched = len(calls) == len(whl)
    offs = [(e[0] - w['completed_wall_ns']) / 1e6 for w, e in zip(calls, whl)] if matched else []
    seq = []
    for e in evs:
        if e[1] == 'WH':
            k = whl.index(e)
            if matched:
                w = calls[k]
                seq.append((e[0], ('T' if w.get('pod_terminating') else 'E') + ':' + w['decision']))
            else:
                seq.append((e[0], 'WH'))
        else:
            seq.append((e[0], e[1]))
    pattern = '|'.join(s for _, s in sorted(seq))
    t = trial_by_aid.get(q['audit_id'])
    o = orow.get(str(t['trial_id'])) if t else None
    ev_calls = [w for w in calls if not w.get('pod_terminating')]
    rec = {'audit_id': q['audit_id'], 'name': q['name'], 'user_agent': q['user_agent'], 'resp': q['resp'],
           'latency_ms': q['latency_ms'], 'n_webhook_lines': len(whl), 'n_webhook_log_calls': len(calls),
           'n_evaluated': len(ev_calls), 'matched_counts': matched,
           'response_line_minus_webhook_completed_ms': offs, 'pattern': pattern,
           'evaluated_pod_rvs': [w.get('pod_resource_version') for w in ev_calls],
           'webhook_request_uids': [w.get('request_uid') for w in calls]}
    if t:
        rec.update(trial_id=t['trial_id'], arm=t['arm'], condition=t['condition'], delta_ms=t['delta_ms'],
                   delete_http_status=t['delete']['http_status'],
                   delete_resp_rv=t['delete']['deleted_object_resource_version'])
    if o:
        rec.update(primary_class=o['primary_class'], flags=o['flags'], delete_rv_source=o['delete_rv_source'])
    recs.append(rec)

wh_total = sum(1 for e in events if e[1] == 'WH')
attributed = sum(r['n_webhook_lines'] for r in recs)
span = (min(q['t_start'] for q in reqs), max(q['t_end'] for q in reqs)) if reqs else (0, 0)
wh_in_span = sum(1 for e in events if e[1] == 'WH' and span[0] - SLACK <= e[0] <= span[1] + SLACK)


def phase_summary(rs):
    c = Counter(r['pattern'] for r in rs)
    return [{'pattern': p, 'n': n} for p, n in c.most_common()]


trial_recs = [r for r in recs if 'trial_id' in r]
a3 = [r for r in trial_recs if r['arm'] == 'A3_dcji_webhook']
non_a3 = [r for r in trial_recs if r['arm'] != 'A3_dcji_webhook']
multi = [r for r in a3 if r['n_evaluated'] >= 2]
differ = [r for r in multi if 'webhook_evaluations_differ' in (r.get('flags') or '')]
# probe cases: every non-trial DELETE that is not the (webhook-exempt) reset identity's fixture delete
probe = [r for r in recs if 'trial_id' not in r and 'e38v2-runner/reset' not in (r['user_agent'] or '')]


def facts(rs):
    """Order facts that the logs show for a set of DELETE requests."""
    f = Counter()
    for r in rs:
        toks = r['pattern'].split('|')
        f['n'] += 1
        f['all_counts_matched'] += r['matched_counts']
        ev_idx = [i for i, x in enumerate(toks) if x.startswith('E:')]
        g = toks.index('GTD') if 'GTD' in toks else None
        f['has_GTD'] += g is not None
        if g is not None and ev_idx:
            f['first_evaluation_before_GTD'] += ev_idx[0] < g
            f['later_evaluations_all_after_GTD'] += all(i > g for i in ev_idx[1:]) if len(ev_idx) > 1 else 0
        f['has_CONF'] += 'CONF' in toks
        f['has_TBU'] += 'TBU' in toks
        f['has_terminating_call'] += any(x.startswith('T:') for x in toks)
        rv = r['evaluated_pod_rvs']
        if len(rv) >= 2:
            f['2nd_evaluation_same_pod_rv_as_1st'] += rv[1] == rv[0]
        if len(ev_idx) >= 2:
            j = ev_idx[1]
            nxt = toks[j + 1:]
            f['after_2nd_evaluation_next_is_CONF'] += bool(nxt) and nxt[0] == 'CONF'
            f['after_2nd_evaluation_next_is_terminating_call'] += bool(nxt) and nxt[0].startswith('T:')
            f['after_2nd_evaluation_nothing_further'] += not nxt
        f['resp_200'] += r['resp'] == '200'
        f['delete_resp_without_rv'] += (r.get('delete_http_status') == 200 and not r.get('delete_resp_rv'))
    return dict(f)


summary = {
    'namespace': NS, 'apiserver_log_lines': n_lines, 'delete_requests_in_ns': len(reqs),
    'webhook_response_lines_total': wh_total, 'webhook_response_lines_attributed_to_a_delete': attributed,
    'webhook_response_lines_within_ns_delete_time_span': wh_in_span,
    'trial_deletes': len(trial_recs), 'probe_or_other_deletes': len(probe),
    'non_webhook_arm_deletes_with_webhook_lines': sum(1 for r in non_a3 if r['n_webhook_lines']),
    'a3_deletes': len(a3), 'a3_counts_matched': sum(r['matched_counts'] for r in a3),
    'a3_multi_evaluation': len(multi), 'a3_multi_evaluation_differ': len(differ),
    'facts_a3_all': facts(a3), 'facts_multi': facts(multi), 'facts_differ': facts(differ),
    'facts_probe': facts(probe),
    'patterns_a3': phase_summary(a3)[:25], 'patterns_differ': phase_summary(differ)[:15],
    'patterns_probe': phase_summary(probe)[:15],
    'response_line_minus_completed_ms': sorted(x for r in a3 for x in r['response_line_minus_webhook_completed_ms'])[::max(1, len(a3) // 20) or 1][:40],
}
json.dump({'summary': summary, 'deletes': recs}, open(out_p, 'w'), indent=1)
print(json.dumps(summary, indent=1))
