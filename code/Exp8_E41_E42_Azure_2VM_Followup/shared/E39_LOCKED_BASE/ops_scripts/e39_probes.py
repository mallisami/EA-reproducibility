#!/usr/bin/env python3
"""E39 verification probes (RUNBOOK section 4), operator tool (not part of the locked runner).
Usage: e39_probes.py E RUN_ID OUT_JSON [--parts a,b,...]
Parts: wh (Pod DELETE webhook), np (NetworkPolicy webhook), a4d (409/200/UID), kl (kubelet status-update rate),
       mirror (A4m lag sample), proxy (delay-proxy calibration), load (load guard), go (client-go),
       order (RV vs time ordering writes).
Every request goes through the loopback API of kind-e39 with the run's ServiceAccount tokens; probe objects live
in the run namespace e39-<RUN_ID> and are removed by the reset identity."""
import json, os, re, statistics, subprocess, sys, tempfile, threading, time

E, RUN_ID, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
PARTS = None
if '--parts' in sys.argv:
    PARTS = set(sys.argv[sys.argv.index('--parts') + 1].split(','))
sys.path.insert(0, E + '/src')
os.chdir(E + '/src')
import e39_kube as K                      # noqa: E402
from e39_kube import E39Client            # noqa: E402
import action_netpol as NP                # noqa: E402
import arms_commit as AC                  # noqa: E402
import e38v2_runner as R38                # noqa: E402

T = f'{E}/.tokens_{RUN_ID}'
NS = 'e39-' + RUN_ID
SRV = open(T + '/server.txt').read().strip()
CA = T + '/ca.crt'
R = f'{E}/results/{RUN_ID}'
OPS = []
RES = {'run_id': RUN_ID, 'namespace': NS, 'server': SRV, 't_start_wall_ns': time.time_ns(), 'parts': {}}
CLIENTS = {}


def cl(ident, pod=K.POD_NAME):
    key = (ident, pod)
    if key not in CLIENTS:
        CLIENTS[key] = E39Client(SRV, NS, ident, token_path=f'{T}/{ident}.token', ca_path=CA, pod_name=pod)
    return CLIENTS[key]


def rv_of(obj):
    return ((obj or {}).get('metadata') or {}).get('resourceVersion')


def rec(op, ident, st, h, obj, **kw):
    d = {'op': op, 'identity': ident, 'audit_id': h.get('Audit-Id') if h else None, 'status': st,
         'rv': rv_of(obj) if st in (200, 201) else None, 't_wall_ns': time.time_ns()}
    if st not in (200, 201) and isinstance(obj, dict):
        d['message'] = obj.get('message')
    d.update(kw)
    OPS.append(d)
    return d


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def patch_cm(ident, name, key, val, op):
    st, h, obj = cl(ident).patch_configmap(name, {key: val})
    return rec(op, ident, st, h, obj, object=name, value=val)


def make_pod(name, webhook=False, anns=None, labels=None):
    c = cl('reset', name)
    pod, _ = c.get_pod()
    if pod is not None:
        c.delete_pod(pod['metadata']['uid'])
        t_end = time.monotonic() + 30
        while time.monotonic() < t_end and c.get_pod()[0] is not None:
            time.sleep(0.05)
    st, h, obj = c.create_target_pod(annotations=anns, extra_labels=labels, webhook=webhook)
    rec('create_pod', 'reset', st, h, obj, pod=name)
    if st not in (200, 201):
        raise RuntimeError('create %s HTTP %s %s' % (name, st, (obj or {}).get('message')))
    uid = obj['metadata']['uid']
    rdy = R38.wait_pod_ready(c, uid, timeout_s=90)
    return uid, rdy


def reset_delete(name):
    c = cl('reset', name)
    pod, _ = c.get_pod()
    if pod is None:
        return None
    st, h, obj = c.delete_pod(pod['metadata']['uid'])
    rec('reset_delete', 'reset', st, h, obj, pod=name)
    return st


def restore_all():
    for n in K.POLICY_CMS:
        cl('reset').patch_configmap(n, {K.CM_VALUE_KEY[n]: 'true'})


def webhook_entries():
    p = f'{E}/results/_webhook_host/{RUN_ID}/webhook_log.jsonl'
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def want(part):
    return PARTS is None or part in PARTS


# ------------------------------------------------------------------ 4.1 Pod DELETE webhook
def part_wh():
    out = {}
    restore_all()
    uids = {n: make_pod(n, webhook=(n != 'probe-wh-nolabel'))[0]
            for n in ('probe-wh-deny', 'probe-wh-nolabel', 'probe-wh-reset', 'probe-wh-scope')}
    patch_cm('writer', 'application-policy', 'allowed', 'false', 'invalidate_app')
    st, h, obj = cl('responder', 'probe-wh-deny').delete_pod(uids['probe-wh-deny'])
    out['invalid_labelled_responder_delete'] = rec('delete', 'responder', st, h, obj, pod='probe-wh-deny', expect=403)
    st, h, obj = cl('responder', 'probe-wh-nolabel').delete_pod(uids['probe-wh-nolabel'])
    out['invalid_unlabelled_responder_delete'] = rec('delete', 'responder', st, h, obj, pod='probe-wh-nolabel', expect=200)
    st, h, obj = cl('reset', 'probe-wh-reset').delete_pod(uids['probe-wh-reset'])
    out['invalid_labelled_reset_delete'] = rec('delete', 'reset', st, h, obj, pod='probe-wh-reset', expect=200)
    patch_cm('writer', 'application-policy', 'allowed', 'true', 'restore_app')
    st, h, obj = cl('responder', 'probe-wh-deny').delete_pod(uids['probe-wh-deny'])
    out['valid_labelled_responder_delete'] = rec('delete', 'responder', st, h, obj, pod='probe-wh-deny', expect=200)
    patch_cm('writer', 'response-scope', 'allowed', 'false', 'invalidate_scope')
    st, h, obj = cl('responder', 'probe-wh-scope').delete_pod(uids['probe-wh-scope'])
    out['scope_false_delete'] = rec('delete', 'responder', st, h, obj, pod='probe-wh-scope', expect=403)
    patch_cm('writer', 'response-scope', 'allowed', 'true', 'restore_scope')
    st, h, obj = cl('responder', 'probe-wh-scope').delete_pod(uids['probe-wh-scope'])
    out['scope_restored_delete'] = rec('delete', 'responder', st, h, obj, pod='probe-wh-scope', expect=200)
    time.sleep(1.0)
    wh = webhook_entries()
    names = {}
    for w in wh:
        n = w.get('pod_name') or w.get('object_name') or w.get('name')
        names.setdefault(n, []).append(w.get('decision'))
    out['webhook_decisions_by_object'] = names
    out['webhook_entry_keys'] = sorted(wh[-1].keys()) if wh else []
    ok = (out['invalid_labelled_responder_delete']['status'] == 403
          and out['invalid_unlabelled_responder_delete']['status'] == 200
          and out['invalid_labelled_reset_delete']['status'] == 200
          and out['valid_labelled_responder_delete']['status'] == 200
          and out['scope_false_delete']['status'] == 403 and out['scope_restored_delete']['status'] == 200
          and 'probe-wh-nolabel' not in names and 'probe-wh-reset' not in names
          and 'probe-wh-deny' in names)
    out['PASS'] = ok
    return out


# ------------------------------------------------------------------ 4.6 NetworkPolicy webhook
def part_np():
    out = {}
    restore_all()
    resp, reset = cl('responder'), cl('reset')
    patch_cm('writer', 'application-policy', 'allowed', 'false', 'invalidate_app')
    f1 = NP.create_isolation_netpol(resp, 'e39-isolate-probe-deny', 'probe-np', webhook=True)
    f2 = NP.create_isolation_netpol(resp, 'e39-isolate-probe-nolabel', 'probe-np', webhook=False)
    patch_cm('writer', 'application-policy', 'allowed', 'true', 'restore_app')
    f3 = NP.create_isolation_netpol(resp, 'e39-isolate-probe-allow', 'probe-np', webhook=True)
    for f, exp in ((f1, 403), (f2, 201), (f3, 201)):
        OPS.append({'op': 'netpol_create', 'identity': 'responder', 'audit_id': f['audit_id'], 'status': f['http_status'],
                    'rv': f['created_resource_version'], 'name': f['name'], 'expect': exp, 't_wall_ns': f['t_send_wall_ns']})
    out['deny'], out['nolabel'], out['allow'] = f1, f2, f3
    time.sleep(1.0)
    wh = webhook_entries()
    names = {}
    for w in wh:
        n = w.get('object_name') or w.get('netpol_name') or w.get('pod_name') or w.get('name')
        names.setdefault(n, []).append(w.get('decision'))
    out['webhook_decisions_by_object'] = {k: v for k, v in names.items() if k and 'isolate' in k}
    out['cleanup'] = {n: NP.cleanup_isolation_netpol(reset, n) for n in
                      ('e39-isolate-probe-deny', 'e39-isolate-probe-nolabel', 'e39-isolate-probe-allow')}
    out['PASS'] = (f1['http_status'] == 403 and f2['http_status'] == 201 and f3['http_status'] == 201
                   and 'e39-isolate-probe-deny' in names and 'e39-isolate-probe-nolabel' not in names
                   and bool(f3['created_resource_version']))
    return out


# ------------------------------------------------------------------ 4.2 A4d 409 probe
RV_MSG = re.compile(r'ResourceVersion in the precondition \((\d+)\) does not match the ResourceVersion in record \((\d+)\)')


def part_a4d():
    out = {}
    uid, rdy = make_pod('probe-a4', anns=AC.fixture_annotations())
    resp, wr = cl('responder', 'probe-a4'), cl('writer', 'probe-a4')
    pod, h = resp.get_pod()
    rv0 = pod['metadata']['resourceVersion']
    st, h, obj = wr.patch_pod_annotations({K.SCOPE_ANN: 'false'})
    w = rec('annotate', 'writer', st, h, obj, pod='probe-a4', object=K.SCOPE_ANN, value='false')
    st, h, obj = resp.delete_pod(uid, rv0)
    d1 = rec('delete_rv_stale', 'responder', st, h, obj, pod='probe-a4', precondition_rv=rv0, expect=409)
    m = RV_MSG.search(d1.get('message') or '')
    out['stale'] = {'rv0': rv0, 'annotate_commit_rv': w['rv'], 'delete': d1,
                    'msg_precondition_rv': m.group(1) if m else None, 'msg_object_rv': m.group(2) if m else None}
    st, h, obj = resp.delete_pod('00000000-0000-0000-0000-000000000000', None)
    out['wrong_uid'] = rec('delete_uid_wrong', 'responder', st, h, obj, pod='probe-a4', expect=409)
    cur = None
    for attempt in range(5):
        pod, _ = resp.get_pod()
        cur = pod['metadata']['resourceVersion']
        st, h, obj = resp.delete_pod(uid, cur)
        d = rec('delete_rv_current', 'responder', st, h, obj, pod='probe-a4', precondition_rv=cur, expect=200,
                attempt=attempt)
        if st in (200, 202) or st != 409:
            break
    out['current'] = d
    out['current_attempts'] = attempt + 1
    out['PASS'] = (d1['status'] == 409 and m is not None and m.group(1) == rv0
                   and int(m.group(2)) >= int(w['rv']) and out['wrong_uid']['status'] == 409
                   and 'UID in the precondition' in (out['wrong_uid'].get('message') or '') and d['status'] == 200)
    out['object_rv_equals_annotate_rv'] = bool(m and m.group(2) == w['rv'])
    return out


# ------------------------------------------------------------------ kubelet status-update rate
def watch_rv(client, seconds, poll_s=0.02):
    ch = []
    last = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        pod, _ = client.get_pod()
        if pod is None:
            break
        rv = pod['metadata']['resourceVersion']
        if last is not None and rv != last:
            ch.append({'t_s': round(time.monotonic() - t0, 4), 'rv': rv})
        last = rv
        time.sleep(poll_s)
    return ch


def part_kl():
    out = {'series': []}
    # 10 fresh pods: RV changes in the 3 s after Ready (the trial window follows Ready immediately)
    for i in range(10):
        name = 'probe-kl-%02d' % i
        uid, rdy = make_pod(name, anns=AC.fixture_annotations())
        ch = watch_rv(cl('responder', name), 3.0)
        out['series'].append({'pod': name, 'uid': uid, 'ready_wait_s': rdy.get('wait_s') if isinstance(rdy, dict) else None,
                              'rv_changes_0_3s': ch})
        reset_delete(name)
    # one pod observed for 60 s after Ready (steady-state rate)
    uid, rdy = make_pod('probe-kl-long')
    ch = watch_rv(cl('responder', 'probe-kl-long'), 60.0)
    out['long'] = {'uid': uid, 'rv_changes_60s': ch, 'rate_per_s': len(ch) / 60.0}
    reset_delete('probe-kl-long')
    n3 = [len(s['rv_changes_0_3s']) for s in out['series']]
    out['summary'] = {'pods_with_rv_change_0_3s': sum(1 for x in n3 if x), 'n_pods': len(n3),
                      'changes_0_3s': n3, 'steady_rate_per_s': out['long']['rate_per_s'],
                      'first_change_times_s': [s['rv_changes_0_3s'][0]['t_s'] for s in out['series'] if s['rv_changes_0_3s']]}
    out['PASS'] = True       # measurement, no acceptance criterion
    return out


# ------------------------------------------------------------------ 4.3 mirror probe (A4m)
def part_mirror():
    out = {'samples': []}
    restore_all()
    uid, rdy = make_pod(K.POD_NAME, anns=AC.fixture_annotations())
    logp = R + '/mirror_probe.jsonl'
    proc = subprocess.Popen([E + '/venv_runner/bin/python', 'mirror_controller.py', '--api', SRV, '--namespace', NS,
                             '--identity', 'mirror', '--token-file', T + '/mirror.token', '--ca-file', CA,
                             '--log', logp, '--duration-s', '90', '--resync-s', '3600'],
                            stdout=open(R + '/mirror_probe_stdout.log', 'w'), stderr=subprocess.STDOUT)
    out['pid'] = proc.pid
    time.sleep(3.0)
    resp = cl('responder')
    for i in range(12):
        val = 'false' if i % 2 == 0 else 'true'
        t0 = time.monotonic_ns()
        w = patch_cm('writer', 'response-scope', 'allowed', val, 'mirror_src_patch')
        seen = None
        while time.monotonic_ns() - t0 < 5e9:
            pod, _ = resp.get_pod()
            if ((pod or {}).get('metadata', {}).get('annotations') or {}).get(K.SCOPE_ANN) == val:
                seen = (time.monotonic_ns() - t0) / 1e6
                break
            time.sleep(0.001)
        out['samples'].append({'value': val, 'src_rv': w['rv'], 'observed_lag_ms_upper': seen,
                               'pod_rv_after': rv_of(pod)})
        time.sleep(0.3)
    patch_cm('writer', 'response-scope', 'allowed', 'true', 'restore_scope')
    time.sleep(1.0)
    pod, _ = resp.get_pod()
    out['converged_true'] = (pod['metadata'].get('annotations') or {}).get(K.SCOPE_ANN) == 'true'
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    ml = [json.loads(l) for l in open(logp) if l.strip()] if os.path.exists(logp) else []
    ups = [m for m in ml if m.get('kind') == 'update']
    out['mirror_log_lines'] = len(ml)
    out['mirror_updates'] = [{k: m.get(k) for k in ('source_commit_rv', 'mirror_commit_rv', 'mirror_audit_id',
                                                     'source_commit_wall_ns', 'mirror_commit_wall_ns')} for m in ups]
    for m in ups:
        if m.get('mirror_audit_id'):
            OPS.append({'op': 'mirror_patch', 'identity': 'mirror', 'audit_id': m['mirror_audit_id'], 'status': 200,
                        'rv': str(m.get('mirror_commit_rv')), 't_wall_ns': m.get('mirror_commit_wall_ns')})
    lags = [s['observed_lag_ms_upper'] for s in out['samples'] if s['observed_lag_ms_upper'] is not None]
    out['observed_lag_ms'] = {'n': len(lags), 'median': statistics.median(lags) if lags else None,
                              'max': max(lags) if lags else None}
    rvok = all(int(m['mirror_commit_rv']) > int(m['source_commit_rv']) for m in ups
               if m.get('mirror_commit_rv') and m.get('source_commit_rv'))
    out['PASS'] = (len(lags) == 12 and max(lags) < 1000 and out['converged_true'] and len(ups) >= 12 and rvok)
    reset_delete(K.POD_NAME)
    return out


# ------------------------------------------------------------------ 4.4 delay proxy calibration
def part_proxy():
    import e39_runner as ER
    from harness_delay_proxy import DelayProxyProcess, calibrate
    out = {}
    tmp = tempfile.mkdtemp(prefix='e39probe_')
    tls = ER.make_loopback_cert(tmp)
    px = DelayProxyProcess(SRV, upstream_ca=CA, match_user_agent='e39-runner/responder', tls_cert=tls[0], tls_key=tls[1])
    try:
        pc = E39Client(px.base_url, NS, 'responder', token_path=T + '/responder.token', ca_path=tls[0])
        dc = cl('responder')
        path = f'/api/v1/namespaces/{NS}/configmaps/evidence-a'
        cal = calibrate(px, pc, dc, path, delays_ms=[0, 50, 200, 1000], n=30)
        out['calibration'] = cal
        acc = []
        for p in cal['per_delay']:
            c = p['requested_ms']
            err = p['median_added_ms'] - c
            ok = abs(err) <= (2.0 if c <= 200 else 0.01 * c)
            acc.append({'c': c, 'median_err_ms': err, 'ok': ok})
        out['acceptance'] = acc
        # non-matching traffic at c = 200: writer GET via proxy (UA mismatch), responder LIST and Pod GET via proxy
        px.set_delay_ms(200)
        wc = E39Client(px.base_url, NS, 'writer', token_path=T + '/writer.token', ca_path=tls[0])

        def timed(c, p, n=10):
            xs = []
            for _ in range(n):
                t0 = time.monotonic_ns()
                c.request('GET', p)
                xs.append((time.monotonic_ns() - t0) / 1e6)
            return statistics.median(xs)
        out['c200_writer_cm_get_ms'] = timed(wc, path)
        out['c200_responder_list_ms'] = timed(pc, f'/api/v1/namespaces/{NS}/configmaps')
        out['c200_responder_pod_get_ms'] = timed(pc, f'/api/v1/namespaces/{NS}/pods/{K.POD_NAME}')
        out['c200_responder_cm_get_ms'] = timed(pc, path, 5)
        px.set_delay_ms(0)
        out['PASS'] = (all(a['ok'] for a in acc) and out['c200_writer_cm_get_ms'] < 50
                       and out['c200_responder_list_ms'] < 50 and out['c200_responder_pod_get_ms'] < 50
                       and out['c200_responder_cm_get_ms'] > 195)
        json.dump(out, open(R + '/delay_proxy_calibration.json', 'w'), indent=1)
    finally:
        px.stop()
    return out


# ------------------------------------------------------------------ 4.5 load guard
def part_load():
    from harness_load import LoadProcess, ensure_objects, load_client_factory, load_object_names
    out = {'trace': []}
    names = load_object_names(8)
    fac = load_client_factory(SRV, names, token_path=T + '/load.token', ca_path=CA)
    c = fac()
    out['created_objects'] = ensure_objects(c, names)
    c.close()
    lp = LoadProcess(guard_kwargs={}, client_factory=fac, names=names, level='idle', n_workers=8, seed=1, log_fn=None)

    def snap(tag):
        s = lp.snapshot()
        s = {k: s.get(k) for k in ('load_level', 'requested_level', 'target_rps', 'load_observed_rps', 'host',
                                   'completed', 'errors', 'dropped_ticks', 'events')}
        s['tag'] = tag
        s['loadavg'] = open('/proc/loadavg').read().split()[:3]
        out['trace'].append(s)
        log('load', tag, s['load_level'], s['load_observed_rps'], s['host'], 'errors', s['errors'])
        return s
    try:
        lp.set_level('moderate')
        for i in range(6):
            time.sleep(10)
            snap('moderate_%ds' % ((i + 1) * 10))
        m = out['trace'][-1]
        lp.set_level('high')
        time.sleep(5)
        snap('high_before_inject')
        lp.inject(mem_available_mb=1500)
        time.sleep(4)
        s_inj = snap('after_inject_mem1500')
        lp.inject(cpu_pct=None, mem_available_mb=None)
        lp.reset_throttle()
        time.sleep(2)
        high_ok = True
        for i in range(6):
            time.sleep(10)
            s = snap('high_%ds' % ((i + 1) * 10))
            cpu = (s['host'] or {}).get('cpu_pct')
            if cpu is not None and cpu > 85:
                high_ok = False
        lp.set_level('idle')
        time.sleep(2)
        snap('idle_end')
    finally:
        out['final'] = lp.stop()
    hs = [t for t in out['trace'] if t['tag'].startswith('high_') and t['tag'][5:6].isdigit()]
    out['summary'] = {'moderate_rps_60s': m['load_observed_rps'], 'moderate_errors': m['errors'],
                      'inject_level': s_inj['load_level'], 'inject_throttled': (s_inj['host'] or {}).get('load_throttled'),
                      'inject_events': len(s_inj['events'] or []),
                      'high_rps_60s': hs[-1]['load_observed_rps'] if hs else None,
                      'high_cpu_max': max(((t['host'] or {}).get('cpu_pct') or 0) for t in hs) if hs else None,
                      'high_cpu_ok': high_ok}
    mr = m['load_observed_rps'] or 0
    out['PASS'] = (45 <= mr <= 55 and s_inj['load_level'] == 'moderate'
                   and (s_inj['host'] or {}).get('load_throttled') is True and len(s_inj['events'] or []) >= 1)
    json.dump(out, open(R + '/load_probe.json', 'w'), indent=1, default=str)
    return out


# ------------------------------------------------------------------ 4.7 client-go
def part_go():
    import goclient_bridge as gb
    out = {}
    restore_all()
    argv = [E + '/src/goclient/bin/e39-goclient', '--server', SRV, '--ca-file', CA, '--token-file', T + '/responder.token',
            '--namespace-prefix', 'e39-']
    uid1, _ = make_pod('probe-go1')
    uid2, _ = make_pod('probe-go2', anns=AC.fixture_annotations())
    uid3, _ = make_pod('probe-go3', anns=AC.fixture_annotations())
    with gb.GoClient(argv) as go:
        out['ping'] = go.ping()
        t0 = time.monotonic_ns()
        g = go.gate_uid(NS, 'probe-go1', {'b': True, 'd': True, 'f': True}, expected_uid=uid1)
        t1 = time.monotonic_ns()
        d = go.delete_uid(NS, 'probe-go1', uid1)
        out['a1_gate'] = g
        out['a1_delete'] = d
        out['a1_gate_end_within_call'] = t0 <= (g.get('t_gate_end_mono_ns') or 0) <= t1
        for n, info in (g.get('reads') or {}).items():
            OPS.append({'op': 'go_get', 'identity': 'responder', 'audit_id': info.get('audit_id'), 'status': 200,
                        'rv': info.get('resourceVersion'), 'object': n})
        OPS.append({'op': 'go_delete_uid', 'identity': 'responder', 'audit_id': d.get('delete_audit_id'),
                    'status': d.get('delete_status'), 'rv': d.get('deleted_resource_version') or None})
        r2 = go.gate_delete_rv(NS, 'probe-go2', uid2)
        out['a4d_combined'] = r2
        OPS.append({'op': 'go_gate_delete_rv', 'identity': 'responder', 'audit_id': r2.get('delete_audit_id'),
                    'status': r2.get('delete_status'), 'rv': r2.get('deleted_resource_version') or None})
        g3 = go.gate_uid(NS, 'probe-go3', {'b': True, 'd': True, 'f': True}, expected_uid=uid3)
        st, h, obj = cl('writer', 'probe-go3').patch_pod_annotations({K.SCOPE_ANN: 'false'})
        w = rec('annotate', 'writer', st, h, obj, pod='probe-go3')
        d3 = go.delete_rv(NS, 'probe-go3', uid3, g3.get('pod_resource_version'))
        out['stale_rv_delete'] = d3
        out['stale_annotate_rv'] = w['rv']
        OPS.append({'op': 'go_delete_rv_stale', 'identity': 'responder', 'audit_id': d3.get('delete_audit_id'),
                    'status': d3.get('delete_status'), 'rv': None, 'message': d3.get('delete_message')})
    out['PASS'] = (out['ping'].get('ok') and g.get('valid') and out['a1_gate_end_within_call']
                   and d.get('delete_status') == 200 and bool(d.get('delete_audit_id')) and d.get('t_send_mono_ns')
                   and d['t_send_mono_ns'] >= g['t_gate_end_mono_ns']
                   and r2.get('delete_status') == 200 and r2.get('valid') and bool(r2.get('t_send_mono_ns'))
                   and d3.get('delete_status') == 409 and 'ResourceVersion in the precondition' in (d3.get('delete_message') or ''))
    reset_delete('probe-go3')
    return out


# ------------------------------------------------------------------ RV vs time ordering
def part_order():
    out = {}
    restore_all()
    vals = ['false', 'true']
    for i in range(100):
        patch_cm('writer', 'evidence-f', 'value', vals[i % 2], 'order_seq')
    patch_cm('writer', 'evidence-f', 'value', 'true', 'order_seq_restore')

    def burst(name):
        c = E39Client(SRV, NS, 'writer', token_path=T + '/writer.token', ca_path=CA)
        for i in range(40):
            st, h, obj = c.patch_configmap(name, {'value': vals[i % 2]})
            rec('order_conc', 'writer', st, h, obj, object=name)
        c.patch_configmap(name, {'value': 'true'})
        c.close()
    th = [threading.Thread(target=burst, args=(n,)) for n in ('evidence-b', 'evidence-d', 'evidence-f', 'evidence-a')]
    [t.start() for t in th]
    [t.join() for t in th]
    restore_all()
    out['n_seq'] = sum(1 for o in OPS if o['op'] == 'order_seq')
    out['n_conc'] = sum(1 for o in OPS if o['op'] == 'order_conc')
    out['PASS'] = True
    return out


PART_FUNCS = [('wh', part_wh), ('np', part_np), ('a4d', part_a4d), ('go', part_go), ('mirror', part_mirror),
              ('order', part_order), ('kl', part_kl), ('proxy', part_proxy), ('load', part_load)]
for name, fn in PART_FUNCS:
    if not want(name):
        continue
    log('== part', name)
    t0 = time.time_ns()
    try:
        r = fn()
    except Exception as exc:
        import traceback
        r = {'PASS': False, 'exception': repr(exc), 'traceback': traceback.format_exc()}
    r['t_start_wall_ns'], r['t_end_wall_ns'] = t0, time.time_ns()
    RES['parts'][name] = r
    log('   PASS' if r.get('PASS') else '   FAIL', name, json.dumps({k: v for k, v in r.items()
                                                                 if k in ('summary', 'acceptance', 'observed_lag_ms', 'exception')}, default=str)[:600])
try:
    restore_all()
except Exception:
    pass
RES['ops'] = OPS
RES['t_end_wall_ns'] = time.time_ns()
RES['ALL_PASS'] = all(p.get('PASS') for p in RES['parts'].values())
json.dump(RES, open(OUT, 'w'), indent=1, default=str)
log('ALL_PASS', RES['ALL_PASS'], {k: bool(v.get('PASS')) for k, v in RES['parts'].items()})
