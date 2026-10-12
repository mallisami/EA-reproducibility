#!/usr/bin/env python3
"""E40 in-cluster wrapper around the (prefix-renamed) E39 runner. Runs INSIDE the AKS runner Pod.

Only what AKS requires is adapted here (protocol/E40_PROTOCOL.txt section 2, DEVIATIONS_E40.txt):
  * API endpoint rail: exactly https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT (the runner Pod's in-cluster
    endpoint) instead of the E39 loopback rail (e39_kube.check_loopback_url is replaced by an exact-match check);
  * design subset (E40): arms A1/A3/A4d, conditions async_scope/async_evidence_last_literal, deltas {0..200} (8);
  * apiserver version v1.36.*; memory guards scaled to the 4 GiB node; E40 LOCK verification; block-wise time box.
Trial logic, gate, writer process, DELETE, records: unchanged E39 code (e39_runner.run_trials / Env).

Modes:  --make-schedule OUT --seed S --run-id R [--reps N]
        --probes --run-id R --output-dir D          (P1-P6, protocol section 5)
        --run --schedule F --run-id R --output-dir D [--lock L | --unlocked-smoke] --cluster-created-epoch T
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.environ.get('E40_ROOT', '/e40'))
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))

import e39_kube as K  # noqa: E402
import e39_runner as R  # noqa: E402
from e39_kube import APIError, E39Client, mono_ns  # noqa: E402

E39_SEED = int(os.environ.get('E40_E39_SEED', '0') or 0)
SA_CA = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
TOKEN_DIR = Path(os.environ.get('E40_TOKEN_DIR', '/tokens'))
API_HOST = (os.environ.get('KUBERNETES_SERVICE_HOST') or '').strip()
API_PORT = (os.environ.get('KUBERNETES_SERVICE_PORT') or '443').strip()
TIME_BOX_S = float(os.environ.get('E40_TIME_BOX_S', str(2 * 3600 + 40 * 60)))


def api_base():
    if not API_HOST:
        raise SystemExit('KUBERNETES_SERVICE_HOST not set: E40 runs only inside the cluster')
    return 'https://%s:%s' % (API_HOST, API_PORT)


def check_incluster_url(base):
    """E40 rail: exact in-cluster endpoint of THIS Pod only (replaces the E39 127.0.0.1 rail)."""
    p = urlsplit(base)
    if (not API_HOST or p.scheme != 'https' or (p.hostname or '') != API_HOST.lower() or p.port is None
            or str(p.port) != API_PORT or p.username or p.password or p.path not in ('', '/')
            or p.query or p.fragment):
        raise ValueError('E40 rail: only https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT is supported')
    return p


K.check_loopback_url = check_incluster_url

# ---- E40 design subset (protocol section 3) -- module globals read at call time by e39_runner
R.ARMS_A = (R.A1, R.A3, R.A4D)
R.ASYNC = ('async_scope', 'async_evidence_last_literal')
R.DELTAS_A = R.DELTAS_SHORT
R.FORBIDDEN_SEEDS = tuple(R.FORBIDDEN_SEEDS) + (424242,) + ((E39_SEED,) if E39_SEED else ())
# 4 GiB AKS node (E39 thresholds were for the shared 16 GiB VM): pause < 512 MiB, abort < 256 MiB
R.MIN_MEM_AVAILABLE_KB = 512 * 1024
R.ABORT_MEM_AVAILABLE_KB = 256 * 1024


def sha256_file(p):
    return R.sha256_file(p)


def log(fh, msg):
    R.log_line(fh, msg)
    if fh is not None:
        print(time.strftime('%H:%M:%S') + ' ' + msg, flush=True)


# =========================================================================== schedule
def make_schedule(a):
    if a.seed is None:
        raise SystemExit('--seed required')
    R.check_seed(a.seed)
    trials = R.build_stage1(a.seed, a.run_id, reps=a.reps, experiments=('A',))
    note = 'E40 AKS replication; design subset arms A1/A3/A4d x {async_scope, async_evidence_last_literal} x ' \
           'deltas {0,2,5,10,20,50,100,200} ms; controls no_write 39/arm, uid_rebind_control 10/arm'
    if a.reps is not None:
        note += '; reps=%d (smoke; NOT the pre-registered design)' % a.reps
    doc = R.schedule_document(trials, 1, a.seed, a.run_id, note=note)
    out = Path(a.make_schedule)
    if out.exists():
        raise SystemExit('refusing to overwrite %s' % out)
    out.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
    cells = {}
    for t in trials:
        k = (t['arm'], t['condition'])
        cells[k] = cells.get(k, 0) + 1
    print(json.dumps({'n_trials': len(trials), 'blocks': len({t['block'] for t in trials}),
                      'per_arm_condition': {'%s/%s' % k: v for k, v in sorted(cells.items())},
                      'sha256': sha256_file(out)}, indent=1))
    return 0


# =========================================================================== LOCK
def verify_e40_lock(lock_path, schedule_path, doc):
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    if not str(lock.get('status', '')).startswith('LOCKED'):
        raise APIError('E40 LOCK status is not LOCKED')
    bad = []
    for rel, h in sorted((lock.get('incluster_files') or {}).items()):
        p = ROOT / rel
        if not p.is_file():
            bad.append('missing ' + rel)
        elif sha256_file(p) != h:
            bad.append('hash ' + rel)
    if not lock.get('incluster_files'):
        bad.append('no incluster_files in LOCK')
    if lock.get('schedule_sha256') != sha256_file(schedule_path):
        bad.append('schedule hash')
    if int(lock.get('schedule_seed', -1)) != int(doc['schedule_seed']):
        bad.append('seed')
    if lock.get('run_id') != doc['run_id']:
        bad.append('run_id')
    if bad:
        raise APIError('E40 LOCK verification failed: ' + '; '.join(bad))
    return lock


# =========================================================================== environment helpers
def mk_client(identity, namespace):
    return E39Client(api_base(), namespace, identity, token_path=str(TOKEN_DIR / ('%s.token' % identity)),
                     ca_path=SA_CA)


def check_identities(namespace):
    who = {}
    for ident in ('responder', 'writer', 'reset', 'mirror'):
        c = mk_client(ident, namespace)
        try:
            who[ident] = c.whoami()
        finally:
            c.close()
    bad = {k: v for k, v in who.items() if v != R.SA_PREFIX + k}
    if bad:
        raise APIError('identity mismatch: %r' % bad)
    return who


# =========================================================================== probes (protocol section 5)
def probes(a):
    import arms_commit as AC
    import e38v2_runner as R38
    ns = R.NS_PREFIX + a.run_id
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    res = {'schema': 'e40.probes.v1', 'started_wall': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'namespace': ns, 'api_base': api_base()}
    reset, resp, wr = mk_client('reset', ns), mk_client('responder', ns), mk_client('writer', ns)
    try:
        # P1 identities + version
        st, _, ver = reset.get_version()
        res['P1'] = {'identities': check_identities(ns), 'version_http': st, 'gitVersion': (ver or {}).get('gitVersion'),
                     'version_ok': str((ver or {}).get('gitVersion', '')).startswith('v1.36'),
                     'kubernetes_service_host': API_HOST, 'kubernetes_service_port': API_PORT}
        valid = R.fixture_values({'condition': 'no_write'})
        R.restore_configmaps(type('E', (), {'reset': reset})(), valid)

        def new_target(webhook=False, anns=None):
            pod, _ = reset.get_pod()
            if pod is not None:
                reset.delete_pod(pod['metadata']['uid'])
                R._wait_gone(reset)
            st, _, obj = reset.create_target_pod(annotations=anns, webhook=webhook)
            if st not in (200, 201):
                raise APIError('probe create HTTP %s %s' % (st, (obj or {}).get('message')))
            uid = obj['metadata']['uid']
            rdy = R38.wait_pod_ready(reset, uid, timeout_s=180)
            return uid, rdy

        # P6 RV order: 50 ConfigMap PATCHes (e38-epoch), 50 Pod annotation PATCHes, PATCH -> DELETE
        cm_rvs = []
        for i in range(50):
            st, _, obj = reset.patch_configmap(K.OBJ_EPOCH, {'sequence': str(2 * i)})
            cm_rvs.append(int(obj['metadata']['resourceVersion']))
        reset.patch_configmap(K.OBJ_EPOCH, {'sequence': '0'})
        uid, rdy = new_target(anns={K.CM_TO_ANN[n]: valid[n] for n in K.POLICY_CMS})
        pod_rvs = []
        for i in range(50):
            st, _, obj = wr.patch_pod_annotations({K.SEQ_ANN: str(i)})
            pod_rvs.append(int(obj['metadata']['resourceVersion']))
        st_w, _, objw = wr.patch_pod_annotations({K.SEQ_ANN: 'last'})
        rv_w = int(objw['metadata']['resourceVersion'])
        t0 = mono_ns()
        st_d, _, objd = resp.delete_pod(uid)
        t1 = mono_ns()
        rv_d = ((objd or {}).get('metadata') or {}).get('resourceVersion')
        res['P6'] = {'cm_patch_rvs_strictly_increasing': all(b > a_ for a_, b in zip(cm_rvs, cm_rvs[1:])),
                     'cm_rv_first_last': [cm_rvs[0], cm_rvs[-1]],
                     'pod_patch_rvs_strictly_increasing': all(b > a_ for a_, b in zip(pod_rvs, pod_rvs[1:])),
                     'pod_rv_first_last': [pod_rvs[0], pod_rvs[-1]],
                     'patch_then_delete': {'patch_rv': rv_w, 'delete_http': st_d, 'delete_response_rv': rv_d,
                                           'delete_rv_gt_patch_rv': (int(rv_d) > rv_w) if rv_d else None,
                                           'delete_rtt_ms': (t1 - t0) / 1e6}}
        R._wait_gone(reset)

        # P3 A4d: stale RV -> 409 naming the RV precondition; current RV -> 200
        uid, rdy = new_target(anns={K.CM_TO_ANN[n]: valid[n] for n in K.POLICY_CMS})
        g = AC.commit_gate(resp, uid)
        rv0 = g['resource_version']
        st_w, _, objw = wr.patch_pod_annotations({K.SCOPE_ANN: 'false'})
        rv1 = objw['metadata']['resourceVersion']
        st409, _, o409 = resp.delete_pod(uid, rv0)
        pod, _ = resp.get_pod()
        rv_now = pod['metadata']['resourceVersion']
        st200, _, o200 = resp.delete_pod(uid, rv_now)
        res['P3'] = {'gate_decision': g['gate']['decision'], 'rv_at_gate': rv0, 'writer_patch_rv': rv1,
                     'stale_delete_http': st409, 'stale_delete_message': (o409 or {}).get('message'),
                     'message_names_rv_precondition': 'ResourceVersion in precondition' in str((o409 or {}).get('message')),
                     'current_rv': rv_now, 'current_delete_http': st200,
                     'current_delete_response_rv': ((o200 or {}).get('metadata') or {}).get('resourceVersion'),
                     'ready_wait_ms': rdy['wait_ms']}
        R._wait_gone(reset)

        # P2 webhook deny / allow on a labelled target
        uid, rdy = new_target(webhook=True)
        reset.patch_configmap(K.OBJ_SCOPE, {'allowed': 'false'})
        st_deny, _, odeny = resp.delete_pod(uid)
        reset.patch_configmap(K.OBJ_SCOPE, {'allowed': 'true'})
        t0 = mono_ns()
        st_allow, _, oallow = resp.delete_pod(uid)
        t1 = mono_ns()
        res['P2'] = {'pod_uid': uid, 'deny_http': st_deny, 'deny_message': (odeny or {}).get('message'),
                     'allow_http': st_allow, 'allow_rtt_ms': (t1 - t0) / 1e6,
                     'ok': (400 <= int(st_deny) < 500) and st_allow in (200, 202)}
        R._wait_gone(reset)

        # P4 + P5: Running/Ready target; kubelet status-update rate over 60 s after Ready
        uid, rdy = new_target(anns={K.CM_TO_ANN[n]: valid[n] for n in K.POLICY_CMS})
        pod, _ = reset.get_pod()
        ps = R38.pod_state(pod)
        rv_ready = pod['metadata']['resourceVersion']
        conn, rsp = reset.open_watch('/api/v1/namespaces/%s/pods' % ns, resource_version=rv_ready,
                                     timeout_seconds=60, socket_timeout_s=75,
                                     extra_query={'fieldSelector': 'metadata.name=%s' % K.POD_NAME})
        events = []
        t_end = time.monotonic() + 62
        try:
            while time.monotonic() < t_end:
                line = rsp.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                md = ((ev.get('object') or {}).get('metadata') or {})
                if ev.get('type') == 'BOOKMARK':
                    continue
                events.append({'type': ev.get('type'), 'rv': md.get('resourceVersion'), 'uid': md.get('uid'),
                               'mono_ns': mono_ns()})
        finally:
            conn.close()
        mods = [e for e in events if e['type'] == 'MODIFIED' and e['uid'] == uid]
        res['P4'] = {'pod_state_at_check': ps, 'ready_wait_ms': rdy['wait_ms'],
                     'ok': ps['phase'] == 'Running' and ps['ready'] is True}
        res['P5'] = {'watch_seconds': 60, 'n_events': len(events), 'n_modified_target': len(mods),
                     'modified_per_minute': len(mods), 'events': events[:50]}
        st, _, _ = reset.delete_pod(uid)
        R._wait_gone(reset)
        R.restore_configmaps(type('E', (), {'reset': reset})(), valid)
    finally:
        for c in (reset, resp, wr):
            c.close()
    res['finished_wall'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (out / 'PROBES_E40.json').write_text(json.dumps(res, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in res.items() if k != 'P5'}, indent=1, sort_keys=True, default=str))
    print('P5 modified events in 60 s:', res['P5']['n_modified_target'])
    return 0


# =========================================================================== run
def run(a):
    doc, specs = R.load_schedule_file(a.schedule)
    if doc['run_id'] != a.run_id:
        raise APIError('schedule run_id %r != --run-id %r' % (doc['run_id'], a.run_id))
    ns = R.NS_PREFIX + a.run_id
    K.check_namespace(ns)
    lock = None
    if a.lock:
        lock = verify_e40_lock(a.lock, a.schedule, doc)
    elif not a.unlocked_smoke:
        raise APIError('run requires --lock (or --unlocked-smoke)')
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    logfh = open(out / 'runner.log', 'a', encoding='utf-8')
    sys.setswitchinterval(5e-5)
    from e38v2_model import frozen_planner_certificate
    cert = frozen_planner_certificate(30.0, R.DEFAULT_F5)
    who = check_identities(ns)
    env = R.Env(base=api_base(), namespace=ns, run_id=a.run_id, seed=doc['schedule_seed'], out_dir=out,
                experiments=['A'], token_dir=TOKEN_DIR, ca_file=SA_CA, logfh=logfh)
    stats_all = {'blocks_done': 0, 'written': 0, 'infra': 0, 'aborted': None, 'time_box_stop': None}
    try:
        st, _, ver = env.reset.get_version()
        if st != 200 or not str((ver or {}).get('gitVersion', '')).startswith('v1.36'):
            raise APIError('unexpected apiserver version: %r' % (ver,))
        st, _, _ = env.reset.get_configmap(K.OBJ_EPOCH)
        if st != 200:
            raise APIError('fixture ConfigMaps missing in %s (HTTP %s)' % (ns, st))
        allowed, _ = env.responder.ssar()
        if not allowed:
            raise APIError('responder SSAR delete pods not allowed')
        session = R.build_session(run_id=a.run_id, stage=1, schedule_seed=doc['schedule_seed'], namespace=ns,
                                  context='aks-incluster', experiments=['A'], e40=True,
                                  identities={k: {'username': v} for k, v in who.items()}, api_server=api_base(),
                                  ca_sha256=sha256_file(SA_CA), apiserver_version=ver.get('gitVersion'),
                                  certificate=cert['certificate'], f5_sha256=cert['f5_sha256'],
                                  schedule_file=str(a.schedule), schedule_sha256=sha256_file(a.schedule),
                                  n_scheduled=len(specs), lock_file=str(a.lock) if a.lock else None,
                                  lock_status=(lock or {}).get('status'), unlocked_smoke=bool(a.unlocked_smoke),
                                  cluster_created_epoch=a.cluster_created_epoch, time_box_s=TIME_BOX_S,
                                  node_name=os.environ.get('NODE_NAME'), pod_name=os.environ.get('POD_NAME'),
                                  design_subset={'arms': list(R.ARMS_A), 'conditions': list(R.ASYNC),
                                                 'deltas_ms': list(R.DELTAS_A)},
                                  mem_guards_kb={'pause_below': R.MIN_MEM_AVAILABLE_KB,
                                                 'abort_below': R.ABORT_MEM_AVAILABLE_KB})
        sp = out / 'SESSION.json'
        if sp.exists():
            sp = out / ('SESSION_resume_%d.json' % int(time.time()))
        sp.write_text(json.dumps(session, indent=2) + '\n', encoding='utf-8')
        log(logfh, 'E40 START run_id=%s trials=%d identities=%s lock=%s api=%s' %
            (a.run_id, len(specs), who, (lock or {}).get('status'), api_base()))
        blocks = []
        for s in specs:
            if s['block'] not in blocks:
                blocks.append(s['block'])
        raw = out / 'RAW_TRIALS.jsonl'
        per_trial = []
        for b in blocks:
            bs = [s for s in specs if s['block'] == b]
            if a.cluster_created_epoch and per_trial:
                mean = sum(per_trial) / len(per_trial)
                proj = time.time() + mean * len(bs)
                if proj - float(a.cluster_created_epoch) > TIME_BOX_S:
                    stats_all['time_box_stop'] = {'before_block': b, 'mean_trial_s': round(mean, 3),
                                                  'cluster_age_s': round(time.time() - float(a.cluster_created_epoch))}
                    log(logfh, 'TIME BOX: stopping before block %s (%r)' % (b, stats_all['time_box_stop']))
                    break
            t0 = time.monotonic()
            st = R.run_trials(env, bs, raw, logfh=logfh, guards=True, stop_file=out / 'STOP')
            el = time.monotonic() - t0
            if st['written']:
                per_trial.append(el / st['written'])
            stats_all['written'] += st['written']
            stats_all['infra'] += st['infra']
            log(logfh, 'BLOCK %s done: %s (cum written %d infra %d)' % (b, json.dumps(st), stats_all['written'],
                                                                         stats_all['infra']))
            if st['aborted']:
                stats_all['aborted'] = st['aborted']
                break
            stats_all['blocks_done'] += 1
            if stats_all['written'] >= 100 and stats_all['infra'] / stats_all['written'] > R.INFRA_ABORT_FRACTION:
                stats_all['aborted'] = 'cumulative infra_error guard'
                break
        log(logfh, 'E40 END ' + json.dumps(stats_all))
        (out / 'RUN_STATS.json').write_text(json.dumps(stats_all, indent=1) + '\n', encoding='utf-8')
        return 0 if stats_all['aborted'] is None else 3
    finally:
        env.close()
        logfh.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--make-schedule')
    ap.add_argument('--probes', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--reps', type=int)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--schedule')
    ap.add_argument('--output-dir')
    ap.add_argument('--lock')
    ap.add_argument('--unlocked-smoke', action='store_true')
    ap.add_argument('--cluster-created-epoch', type=float)
    a = ap.parse_args(argv)
    if a.make_schedule:
        return make_schedule(a)
    if a.probes:
        return probes(a)
    if a.run:
        return run(a)
    ap.error('choose --make-schedule, --probes or --run')


if __name__ == '__main__':
    sys.exit(main())
