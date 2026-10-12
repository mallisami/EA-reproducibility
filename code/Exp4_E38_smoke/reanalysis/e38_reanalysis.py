#!/usr/bin/env python3
"""E38 smoke re-analysis (no cluster access). Reads RAW_TRIALS.jsonl etc., writes CSV/JSON to out/e38."""
import json, math, sys, statistics as st
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path.home() / 'dcji_reanalysis_20260926'
RAW = BASE / 'inputs/e38raw'
OUT = BASE / 'out/e38'
OUT.mkdir(parents=True, exist_ok=True)
rows = [json.loads(l) for l in open(RAW / 'RAW_TRIALS.jsonl') if l.strip()]
session = json.load(open(RAW / 'SESSION.json'))
setup = json.load(open(RAW / 'SETUP_TRACE.json'))
assert len(rows) == 56

POSTGATE = {'postgate_uid', 'postgate_evidence', 'postgate_scope', 'postgate_application_policy'}
NAME = 'e38-target'

def last(path_frag, reqs, method='GET'):
    xs = [q for q in reqs if q['method'] == method and path_frag in q['path']]
    return xs[-1] if xs else None

def summ(x):
    x = [v for v in x if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not x:
        return dict(n=0, mean=None, median=None, min=None, max=None, sd=None)
    return dict(n=len(x), mean=float(np.mean(x)), median=float(np.median(x)), min=float(np.min(x)),
                max=float(np.max(x)), sd=float(np.std(x, ddof=1)) if len(x) > 1 else None)

# ---------------------------------------------------------------- per-trial table
trial_rows = []
for r in rows:
    g = r.get('guard') or {}
    reqs = r['all_requests']
    guard_reqs = [q for q in reqs if q['kind'] == 'guard' and q['method'] != 'DELETE']
    delete = [q for q in reqs if q['kind'] == 'guard' and q['method'] == 'DELETE']
    delete = delete[0] if delete else None
    ev = r['events'][-1] if r['events'] else {}
    oag = r.get('oracle_at_gate_preacquisition') or {}
    oau = r.get('oracle_at_use') or {}
    model = g.get('model') or {}
    trial_rows.append(dict(
        trial_id=r['trial_id'], ordinal=int(r['trial_id'][-6:]), condition=r['condition'], method=r['method'],
        rep=r['rep_index'], method_order='>'.join(r['method_order']), status=r['status'],
        gate_decision=r.get('guard_decision'), gate_reason=g.get('reason'),
        coherent=g.get('coherent'), epoch_before=g.get('epoch_before'), epoch_after=g.get('epoch_after'),
        unfenced_hybrid_decision=model.get('decision'), hybrid_J=model.get('justification'),
        oracle_gate=r.get('oracle_gate_decision'), oracle_preacq=oag.get('decision'),
        oracle_at_use=oau.get('decision'), oracle_at_use_source=oau.get('oracle_source', 'physical get_oracle()'),
        http_status=r.get('server_http_status'), delete_sent=r.get('delete_sent'),
        effect_verified=(r.get('effect') or {}).get('effect_verified'),
        safety_notes='; '.join(r.get('safety_notes') or []),
        injector_kind=ev.get('kind'), injector_span_ms=(ev['finished_ms'] - ev['started_ms']) if ev.get('finished_ms') else None,
        injector_started_ms=ev.get('started_ms'), injector_finished_ms=ev.get('finished_ms'),
        queried_evidence=','.join(g.get('queried_evidence') or []),
        refresh_latency_ms=g.get('refresh_latency_ms'), gate_latency_ms=g.get('gate_latency_ms'),
        decision_to_first_effect_ms=r.get('decision_to_first_effect_ms'),
        guard_get_count=g.get('guard_get_count'), core_evidence_get_count=g.get('core_evidence_get_count'),
        elapsed_wall_ms=r['elapsed_wall_ms'],
        first_req_t0=reqs[0]['t0_ms'], last_req_t1=reqs[-1]['t1_ms'],
        first_guard_t0=guard_reqs[0]['t0_ms'] if guard_reqs else None,
        delete_t0=delete['t0_ms'] if delete else None, delete_t1=delete['t1_ms'] if delete else None,
        action_dispatch_ms=r.get('action_dispatch_ms'),
    ))
T = pd.DataFrame(trial_rows).sort_values('ordinal')

def category(x):
    c, m, d = x.condition, x.method, x.gate_decision
    if d == 'UNKNOWN_COHERENCE':
        return 'UNKNOWN_open_epoch(status UNKNOWN)' if c == 'unknown_open_epoch' else 'UNKNOWN_during_acq(status COMPLETE)'
    if d == 'EXECUTE' and x.oracle_at_use == 'EXECUTE':
        return 'valid_control_executed'
    if d == 'EXECUTE' and x.http_status == 409:
        return 'postgate_UID_409_blocked'
    if d == 'EXECUTE' and x.oracle_at_use != 'EXECUTE' and c in POSTGATE:
        return 'postgate_invalid_accepted'
    if d == 'EXECUTE' and x.oracle_gate != 'EXECUTE':
        return 'symmetric_gate_miss_invalid_accepted'
    if d == 'ABORT_REPLAN' and x.oracle_gate == 'ABORT_REPLAN':
        return 'correct_abort'
    return 'OTHER'
T['category'] = T.apply(category, axis=1)
cols = ['condition', 'method', 'rep', 'trial_id', 'method_order', 'gate_decision', 'oracle_preacq', 'oracle_gate', 'oracle_at_use',
        'unfenced_hybrid_decision', 'hybrid_J', 'coherent', 'epoch_before', 'epoch_after', 'http_status', 'delete_sent',
        'effect_verified', 'status', 'category', 'injector_kind', 'injector_span_ms', 'queried_evidence',
        'refresh_latency_ms', 'gate_latency_ms', 'decision_to_first_effect_ms', 'elapsed_wall_ms', 'safety_notes',
        'oracle_at_use_source']
T[cols].to_csv(OUT / 'E38_TRIAL_TABLE_56.csv', index=False)

cond_order = session['conditions']
cell = []
for c in cond_order:
    for m in ('DCJI', 'FullRefresh'):
        s = T[(T.condition == c) & (T.method == m)].sort_values('rep')
        d = dict(condition=c, method=m)
        for _, x in s.iterrows():
            k = f'r{x.rep}'
            d[f'{k}_gate'] = x.gate_decision; d[f'{k}_oracle_gate'] = x.oracle_gate; d[f'{k}_oracle_use'] = x.oracle_at_use
            d[f'{k}_http'] = x.http_status; d[f'{k}_effect'] = x.effect_verified; d[f'{k}_status'] = x.status
        d['category'] = '|'.join(sorted(set(s.category)))
        cell.append(d)
C = pd.DataFrame(cell)
C.to_csv(OUT / 'E38_CELL_TABLE_28.csv', index=False)
cat_counts = T.category.value_counts().to_dict()

# unknown & during_acq details
U = T[T.gate_decision == 'UNKNOWN_COHERENCE'][['condition', 'method', 'rep', 'trial_id', 'status', 'gate_reason', 'epoch_before',
      'epoch_after', 'oracle_preacq', 'oracle_gate', 'oracle_at_use', 'unfenced_hybrid_decision', 'hybrid_J', 'queried_evidence']]
da_detail = []
for r in rows:
    if r['condition'] != 'during_acquisition':
        continue
    g = r['guard']
    ev = r['events'][-1]
    reads = [(q['path'].rsplit('/', 1)[-1], q['t0_ms'], q['t1_ms']) for q in g['guard_requests'] if q['method'] == 'GET']
    da_detail.append(dict(trial_id=r['trial_id'], method=r['method'], rep=r['rep_index'],
        measured_evidence=json.dumps(g['measured_evidence'], sort_keys=True),
        hybrid_evidence=json.dumps({k: g['measured_evidence'].get(k, True) for k in 'abcdef'}, sort_keys=True),
        hybrid_J=g['model']['justification'], unfenced_decision=g['model']['decision'],
        epoch_before=g['epoch_before'], epoch_after=g['epoch_after'], fenced_decision=g['decision'],
        injector_span_ms=ev['finished_ms'] - ev['started_ms'],
        injector_start_minus_guard_first_t0_ms=ev['started_ms'] - reads[0][1],
        oracle_gate=r['oracle_gate_decision'], oracle_at_use=r['oracle_at_use']['decision']))
DA = pd.DataFrame(da_detail)
U.to_csv(OUT / 'E38_UNKNOWN_COHERENCE_8.csv', index=False)
DA.to_csv(OUT / 'E38_DURING_ACQ_UNFENCED.csv', index=False)

# ---------------------------------------------------------------- latency
L = T[T.refresh_latency_ms.notna()].copy()
lat = []
subsets = {'a_all_timing_bearing': L,
           'b_excl_during_acquisition': L[L.condition != 'during_acquisition'],
           'c_natural_stable_irrelevant': L[L.condition.isin(['stable', 'irrelevant'])],
           'd_no_injector_in_or_before_guard_stable_only': L[L.condition == 'stable']}
for name, S in subsets.items():
    for m in ('DCJI', 'FullRefresh'):
        for metric in ('refresh_latency_ms', 'gate_latency_ms'):
            d = summ(S[S.method == m][metric].tolist())
            lat.append(dict(subset=name, method=m, metric=metric, **d))
LAT = pd.DataFrame(lat)
LAT.to_csv(OUT / 'E38_LATENCY.csv', index=False)

rng = np.random.default_rng(38)
B = 20000
diffs = []
for name, S in list(subsets.items())[:2]:
    conds = sorted(set(S.condition))
    for metric in ('refresh_latency_ms', 'gate_latency_ms'):
        per = {}
        for c in conds:
            a = S[(S.condition == c) & (S.method == 'DCJI')][metric].to_numpy()
            b = S[(S.condition == c) & (S.method == 'FullRefresh')][metric].to_numpy()
            per[c] = (a, b)
        point = float(np.mean([a.mean() - b.mean() for a, b in per.values()]))
        boots = np.zeros(B)
        for c, (a, b) in per.items():
            ia = rng.integers(0, len(a), (B, len(a))); ib = rng.integers(0, len(b), (B, len(b)))
            boots += a[ia].mean(1) - b[ib].mean(1)
        boots /= len(per)
        # paired by rep (method order randomized within rep)
        pd_ = []
        for c in conds:
            for rp in (0, 1):
                a = S[(S.condition == c) & (S.method == 'DCJI') & (S.rep == rp)][metric]
                b = S[(S.condition == c) & (S.method == 'FullRefresh') & (S.rep == rp)][metric]
                if len(a) and len(b):
                    pd_.append(float(a.iloc[0] - b.iloc[0]))
        diffs.append(dict(subset=name, metric=metric, n_conditions=len(per),
            condition_matched_mean_diff_ms=point, boot_ci95_lo=float(np.percentile(boots, 2.5)),
            boot_ci95_hi=float(np.percentile(boots, 97.5)), boot_B=B,
            n_rep_pairs=len(pd_), paired_mean_diff=float(np.mean(pd_)), paired_median_diff=float(np.median(pd_)),
            pairs_DCJI_slower=int(sum(x > 0 for x in pd_)),
            pooled_mean_diff=float(S[S.method == 'DCJI'][metric].mean() - S[S.method == 'FullRefresh'][metric].mean())))
        for c, (a, b) in per.items():
            diffs.append(dict(subset=name, metric=metric, condition=c, n_conditions=1,
                condition_matched_mean_diff_ms=float(a.mean() - b.mean())))
DIFF = pd.DataFrame(diffs)
DIFF.to_csv(OUT / 'E38_LATENCY_DIFF.csv', index=False)

# ---------------------------------------------------------------- exposure windows
X = []
for r in rows:
    if r.get('guard_decision') != 'EXECUTE':
        continue
    g = r['guard']; reqs = r['all_requests']
    greq = [q for q in g['guard_requests'] if q['method'] == 'GET']
    ssar = [q for q in g['guard_requests'] if q['method'] == 'POST'][-1]
    ev_gets = [q for q in greq if '/configmaps/obs-' in q['path']]
    scope_get = last('/configmaps/scope-policy', greq)
    app_get = last('/configmaps/application-policy', greq)
    pod_get = last('/pods/' + NAME, greq)
    epoch_gets = [q for q in greq if q['path'].endswith('/e38-epoch')]
    dele = [q for q in reqs if q['kind'] == 'guard' and q['method'] == 'DELETE'][0]
    eff = [q for q in reqs if q['kind'] == 'effect']
    eff404 = [q for q in eff if q['status'] == 404]
    first_t0 = greq[0]['t0_ms']
    gate_end = first_t0 + g['gate_latency_ms']       # guard t0 ~= first guard request t0 (upper bound, us-level)
    tdel = r['action_dispatch_ms']
    last_t1 = reqs[-1]['t1_ms']
    dec_started_rec = (last_t1 - r['decision_to_first_effect_ms']) if r.get('decision_to_first_effect_ms') else None
    evn = r['events'][-1] if r['events'] else None
    postg = r['condition'] in POSTGATE
    inj = (evn['finished_ms'] - evn['started_ms']) if (postg and evn) else 0.0
    inj_between = bool(postg and evn and evn['started_ms'] >= gate_end - 0.5 and evn['finished_ms'] <= tdel)
    mut_reqs = [q for q in reqs if q['kind'] == 'mutation' and q['t0_ms'] >= gate_end - 0.5 and q['t1_ms'] <= tdel]
    # also setup-kind requests (pod recreate inside uid_rebind injector) between gate and delete
    other_between = [q for q in reqs if q['kind'] not in ('guard', 'mutation') and q['t0_ms'] >= gate_end - 0.5 and q['t1_ms'] <= tdel]
    d = dict(trial_id=r['trial_id'], condition=r['condition'], method=r['method'], rep=r['rep_index'],
        group='postgate_injected' if postg else 'natural_no_injector_between_gate_and_DELETE',
        http_status=r['server_http_status'], effect_verified=r['effect']['effect_verified'],
        oracle_at_use=r['oracle_at_use']['decision'],
        gate_latency_ms=g['gate_latency_ms'],
        gate_end_est_minus_ssar_t1_ms=gate_end - ssar['t1_ms'],
        guard_t0_rec_offset_ms=(first_t0 - dec_started_rec) if dec_started_rec else None,
        dispatch_minus_delete_t0_ms=dele['t0_ms'] - tdel,
        injector_span_ms=inj, injector_between_gate_and_delete=inj_between,
        n_injector_requests_between=len(mut_reqs) + len(other_between),
        a_gate_end_to_DELETE_send=tdel - gate_end,
        b_last_evidence_GET_resp_to_DELETE_send=tdel - ev_gets[-1]['t1_ms'],
        b_last_evidence_key=ev_gets[-1]['path'].rsplit('/', 1)[-1],
        b2_first_evidence_GET_req_to_DELETE_resp=dele['t1_ms'] - ev_gets[0]['t0_ms'],
        c_scope_GET_resp_to_DELETE_send=tdel - scope_get['t1_ms'],
        d_app_policy_GET_resp_to_DELETE_send=tdel - app_get['t1_ms'],
        uid_pod_GET_resp_to_DELETE_send=tdel - pod_get['t1_ms'],
        fence_close_epoch_GET_resp_to_DELETE_send=tdel - epoch_gets[-1]['t1_ms'],
        ssar_resp_to_DELETE_send=tdel - ssar['t1_ms'],
        e_first_guard_GET_start_to_DELETE_resp=dele['t1_ms'] - first_t0,
        f_DELETE_send_to_DELETE_resp=dele['t1_ms'] - tdel,
        g_DELETE_resp_to_effect_verified=(eff404[0]['t1_ms'] - dele['t1_ms']) if eff404 and r['effect']['effect_verified'] else None,
        effect_elapsed_ms_logged=r['effect']['effect_elapsed_ms'],
        decision_to_first_effect_ms=r.get('decision_to_first_effect_ms'),
        guard_start_to_effect_excl_injector_ms=(r['decision_to_first_effect_ms'] - inj) if r.get('decision_to_first_effect_ms') else None,
    )
    for k in ('a_gate_end_to_DELETE_send', 'b_last_evidence_GET_resp_to_DELETE_send', 'c_scope_GET_resp_to_DELETE_send',
              'd_app_policy_GET_resp_to_DELETE_send', 'e_first_guard_GET_start_to_DELETE_resp'):
        d[k + '_minus_injector'] = d[k] - inj
    X.append(d)
XW = pd.DataFrame(X)
XW.to_csv(OUT / 'E38_EXPOSURE_WINDOWS_PER_TRIAL.csv', index=False)
wcols = ['a_gate_end_to_DELETE_send', 'b_last_evidence_GET_resp_to_DELETE_send', 'c_scope_GET_resp_to_DELETE_send',
         'd_app_policy_GET_resp_to_DELETE_send', 'uid_pod_GET_resp_to_DELETE_send', 'fence_close_epoch_GET_resp_to_DELETE_send',
         'e_first_guard_GET_start_to_DELETE_resp', 'f_DELETE_send_to_DELETE_resp', 'g_DELETE_resp_to_effect_verified',
         'injector_span_ms', 'a_gate_end_to_DELETE_send_minus_injector', 'b_last_evidence_GET_resp_to_DELETE_send_minus_injector',
         'c_scope_GET_resp_to_DELETE_send_minus_injector', 'd_app_policy_GET_resp_to_DELETE_send_minus_injector',
         'e_first_guard_GET_start_to_DELETE_resp_minus_injector', 'decision_to_first_effect_ms',
         'guard_start_to_effect_excl_injector_ms']
ws = []
for grp in ['natural_no_injector_between_gate_and_DELETE', 'postgate_injected', 'ALL']:
    for m in ('DCJI', 'FullRefresh', 'both'):
        S = XW if grp == 'ALL' else XW[XW.group == grp]
        if m != 'both':
            S = S[S.method == m]
        for w in wcols:
            ws.append(dict(group=grp, method=m, window=w, **summ(S[w].tolist())))
WS = pd.DataFrame(ws)
WS.to_csv(OUT / 'E38_EXPOSURE_WINDOWS_SUMMARY.csv', index=False)

# request RTTs (for writer-latency sizing)
rtt = {}
allreq = [q for r in rows for q in r['all_requests']]
for lab, sel in [('PATCH_configmap_all', lambda q: q['method'] == 'PATCH'),
                 ('PATCH_configmap_mutation', lambda q: q['method'] == 'PATCH' and q['kind'] == 'mutation'),
                 ('GET_configmap_guard', lambda q: q['method'] == 'GET' and q['kind'] == 'guard' and '/configmaps/' in q['path']),
                 ('GET_pod_guard', lambda q: q['method'] == 'GET' and q['kind'] == 'guard' and '/pods/' in q['path']),
                 ('SSAR_guard', lambda q: q['method'] == 'POST' and q['kind'] == 'guard'),
                 ('DELETE_guard_200', lambda q: q['method'] == 'DELETE' and q['kind'] == 'guard' and q['status'] == 200),
                 ('DELETE_guard_409', lambda q: q['method'] == 'DELETE' and q['kind'] == 'guard' and q['status'] == 409),
                 ('DELETE_reset', lambda q: q['method'] == 'DELETE' and q['kind'] == 'reset')]:
    rtt[lab] = summ([q['t1_ms'] - q['t0_ms'] for q in allreq if sel(q)])
    xs = [q['t1_ms'] - q['t0_ms'] for q in allreq if sel(q)]
    rtt[lab]['p90'] = float(np.percentile(xs, 90)) if xs else None
pd.DataFrame([dict(request=k, **v) for k, v in rtt.items()]).to_csv(OUT / 'E38_REQUEST_RTT.csv', index=False)

# ---------------------------------------------------------------- symmetric detail
sym = []
for r in rows:
    if r['condition'] != 'symmetric_bdf_stress':
        continue
    g = r['guard']; og = r['oracle_at_gate_preacquisition']
    measured = g['measured_evidence']
    sym.append(dict(trial_id=r['trial_id'], method=r['method'], rep=r['rep_index'],
        queried=','.join(g['queried_evidence']) + ',uid,scope,app_policy,epoch x2,SSAR',
        measured=json.dumps(measured, sort_keys=True),
        cached_used=json.dumps({k: True for k in 'abcdef' if k not in measured}, sort_keys=True),
        hybrid=json.dumps({k: measured.get(k, True) for k in 'abcdef'}, sort_keys=True),
        hybrid_J=g['model']['justification'], gate_decision=g['decision'], coherent=g['coherent'],
        physical_evidence=json.dumps(og['physical_evidence'], sort_keys=True), physical_J=og['justification'],
        oracle_gate=r['oracle_gate_decision'], oracle_at_use=r['oracle_at_use']['decision'],
        http=r['server_http_status'], effect_verified=r['effect']['effect_verified'],
        safety_notes='; '.join(r['safety_notes'])))
SYM = pd.DataFrame(sym)
SYM.to_csv(OUT / 'E38_SYMMETRIC_DETAIL.csv', index=False)

# ---------------------------------------------------------------- run timing
trial_first = min(q['t0_ms'] for q in rows[0]['all_requests'])
trial_last = max(q['t1_ms'] for q in rows[-1]['all_requests'])
by_cond_wall = T.groupby('condition').elapsed_wall_ms.mean().to_dict()
timing = dict(setup_first_t0=setup[0]['t0_ms'], setup_last_t1=setup[-1]['t1_ms'],
    setup_span_s=(setup[-1]['t1_ms'] - setup[0]['t0_ms']) / 1000,
    trials_span_s=(trial_last - trial_first) / 1000, sum_elapsed_wall_s=T.elapsed_wall_ms.sum() / 1000,
    mean_s_per_trial=T.elapsed_wall_ms.mean() / 1000,
    mean_s_per_trial_excl_uid_rebind_conditions=T[~T.condition.isin(['uid_rebind', 'postgate_uid'])].elapsed_wall_ms.mean() / 1000,
    mean_s_per_trial_uid_rebind_conditions=T[T.condition.isin(['uid_rebind', 'postgate_uid'])].elapsed_wall_ms.mean() / 1000,
    session_wall_s='09:40:24Z->09:42:36Z = 132 s (UTC second resolution)',
    by_condition_mean_wall_ms=by_cond_wall)

# ---------------------------------------------------------------- verification vs manuscript
def g_(subset, m, metric, stat):
    return float(LAT[(LAT.subset == subset) & (LAT.method == m) & (LAT.metric == metric)][stat].iloc[0])
ver = dict(
    categories=cat_counts,
    unknown_coherence_total=int((T.gate_decision == 'UNKNOWN_COHERENCE').sum()),
    unknown_status_UNKNOWN=int((T.status == 'UNKNOWN').sum()),
    refresh_mean_all=(g_('a_all_timing_bearing', 'DCJI', 'refresh_latency_ms', 'mean'), g_('a_all_timing_bearing', 'FullRefresh', 'refresh_latency_ms', 'mean')),
    gate_mean_all=(g_('a_all_timing_bearing', 'DCJI', 'gate_latency_ms', 'mean'), g_('a_all_timing_bearing', 'FullRefresh', 'gate_latency_ms', 'mean')),
    refresh_mean_excl=(g_('b_excl_during_acquisition', 'DCJI', 'refresh_latency_ms', 'mean'), g_('b_excl_during_acquisition', 'FullRefresh', 'refresh_latency_ms', 'mean')),
    gate_mean_excl=(g_('b_excl_during_acquisition', 'DCJI', 'gate_latency_ms', 'mean'), g_('b_excl_during_acquisition', 'FullRefresh', 'gate_latency_ms', 'mean')),
    max_decision_to_first_effect=float(T.decision_to_first_effect_ms.max()),
    max_decision_to_first_effect_trial=T.loc[T.decision_to_first_effect_ms.idxmax(), 'trial_id'],
    max_guard_start_to_effect_excl_injector=float(XW.guard_start_to_effect_excl_injector_ms.max()),
    max_guard_start_to_effect_excl_injector_trial=XW.loc[XW.guard_start_to_effect_excl_injector_ms.idxmax(), 'trial_id'],
    timing=timing, rtt=rtt)
json.dump(ver, open(OUT / 'E38_VERIFICATION.json', 'w'), indent=2, default=str)

# ---------------------------------------------------------------- console dump
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 400)
print(T.category.value_counts())
print(T[['condition', 'method', 'rep', 'gate_decision', 'oracle_gate', 'oracle_at_use', 'unfenced_hybrid_decision', 'http_status', 'effect_verified', 'status', 'category']].to_string())
print(U.to_string()); print(DA.to_string())
print(LAT.to_string()); print(DIFF[DIFF.condition.isna()].to_string() if 'condition' in DIFF else DIFF.to_string())
print(XW[['trial_id', 'condition', 'method', 'group', 'http_status', 'injector_span_ms', 'injector_between_gate_and_delete', 'n_injector_requests_between',
          'gate_end_est_minus_ssar_t1_ms', 'guard_t0_rec_offset_ms', 'dispatch_minus_delete_t0_ms', 'a_gate_end_to_DELETE_send', 'a_gate_end_to_DELETE_send_minus_injector',
          'b_last_evidence_GET_resp_to_DELETE_send', 'c_scope_GET_resp_to_DELETE_send', 'd_app_policy_GET_resp_to_DELETE_send',
          'fence_close_epoch_GET_resp_to_DELETE_send', 'e_first_guard_GET_start_to_DELETE_resp', 'f_DELETE_send_to_DELETE_resp',
          'g_DELETE_resp_to_effect_verified', 'decision_to_first_effect_ms', 'guard_start_to_effect_excl_injector_ms']].to_string())
print(WS[WS.method != 'both'].to_string())
print(SYM.to_string())
print(json.dumps(ver, indent=1, default=str))
