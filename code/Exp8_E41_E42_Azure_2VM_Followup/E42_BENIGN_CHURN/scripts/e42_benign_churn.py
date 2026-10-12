#!/usr/bin/env python3
"""E42 benign target-Pod churn experiment.

Purpose: measure the availability cost of resourceVersion binding when the target Pod
changes for a reason unrelated to the security predicate. The benign writer changes only
the inherited E39 sequence annotation (e38.seq); it never changes evidence, scope, policy,
UID, or any predicate in V_core.

Arms: A1 (UID-only DELETE after DCJI gate) and A4d (target-resident validity + UID/RV
precondition). One benign Pod PATCH is scheduled delta ms after gate end. The no-write
control quantifies unrelated background/kubelet resourceVersion churn.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PKG = HERE.parents[2]
BASE = PKG / 'shared' / 'E39_LOCKED_BASE'
SRC = BASE / 'src'
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PKG / 'tools'))

import arms_commit as AC  # noqa: E402
import e39_kube as K  # noqa: E402
import e39_runner as R  # noqa: E402
from e39_kube import APIError, E39Client  # noqa: E402
from harness_arrivals import ArrivalWriter  # noqa: E402
from lock_util import verify_lock  # noqa: E402

API_BASE = os.environ.get('E41_API_SERVER', 'https://127.0.0.1:6443')
CA_FILE = os.environ.get('E41_CA_FILE', '/var/lib/rancher/k3s/server/tls/server-ca.crt')
TOKEN_DIR = Path(os.environ.get('E41_TOKEN_DIR', str(PKG / 'runtime' / 'tokens')))
WORKER_LABEL_KEY = os.environ.get('E41_WORKER_LABEL_KEY', 'e41-role')
WORKER_LABEL_VALUE = os.environ.get('E41_WORKER_LABEL_VALUE', 'workload')
EXPECTED_VERSION_PREFIX = os.environ.get('E41_K8S_PREFIX', 'v1.37')

ARMS = (R.A1, R.A4D)
DELTAS = (0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
N_PER_CELL = 39
SEED_FORBIDDEN = set(R.FORBIDDEN_SEEDS) | {40926017, 972231175, 93041271}
COND_BENIGN = 'benign_target_annotation_churn'
COND_NOWRITE = 'no_write'

R.MIN_MEM_AVAILABLE_KB = 768 * 1024
R.ABORT_MEM_AVAILABLE_KB = 512 * 1024


def _create_target_pod_multihost(self, annotations=None, extra_labels=None, webhook=False,
                                 image='registry.k8s.io/pause:3.10'):
    labels = {'app': 'e39', 'e39-managed': 'true'}
    labels.update(extra_labels or {})
    if webhook:
        labels[K.WEBHOOK_LABEL[0]] = K.WEBHOOK_LABEL[1]
    md = {'name': self.pod_name, 'namespace': self.ns, 'labels': labels}
    if annotations:
        md['annotations'] = {k: str(v) for k, v in annotations.items()}
    body = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': md,
            'spec': {'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 0,
                     'automountServiceAccountToken': False, 'enableServiceLinks': False,
                     'nodeSelector': {WORKER_LABEL_KEY: WORKER_LABEL_VALUE},
                     'containers': [{'name': 'target', 'image': image, 'imagePullPolicy': 'IfNotPresent'}]}}
    return self.request('POST', f'{self._ns_root()}/pods', body)

K.E39Client.create_target_pod = _create_target_pod_multihost


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def make_schedule(args):
    if args.seed is None:
        raise SystemExit('--seed required')
    if args.seed in SEED_FORBIDDEN:
        raise SystemExit('seed is reused/forbidden')
    rng = random.Random(args.seed)
    n_per_cell = N_PER_CELL if args.reps is None else int(args.reps)
    if n_per_cell < 1 or n_per_cell > N_PER_CELL:
        raise SystemExit(f'--reps must be 1..{N_PER_CELL}')
    trials, tid = [], 1
    for block in range(n_per_cell):
        b = []
        for arm in ARMS:
            for d in DELTAS:
                b.append({'schema': 'e42.schedule.item.v1', 'run_id': args.run_id, 'trial_id': tid,
                          'block': block, 'rep': block, 'arm': arm, 'condition': COND_BENIGN,
                          'delta_ms': d, 'schedule_seed': args.seed})
                tid += 1
            b.append({'schema': 'e42.schedule.item.v1', 'run_id': args.run_id, 'trial_id': tid,
                      'block': block, 'rep': block, 'arm': arm, 'condition': COND_NOWRITE,
                      'delta_ms': None, 'schedule_seed': args.seed})
            tid += 1
        rng.shuffle(b)
        trials.extend(b)
    doc = {'schema': 'e42.schedule.v1', 'run_id': args.run_id, 'schedule_seed': args.seed,
           'design': {'arms': list(ARMS), 'deltas_ms': list(DELTAS), 'n_per_cell': n_per_cell,
                      'conditions': [COND_BENIGN, COND_NOWRITE], 'stage2_topup': False},
           'n_trials': len(trials), 'trials': trials}
    out = Path(args.make_schedule)
    if out.exists():
        raise SystemExit(f'refusing to overwrite {out}')
    out.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'n_trials': len(trials), 'sha256': sha256_file(out)}, indent=1))
    return 0


def mk_client(identity, ns):
    return E39Client(API_BASE, ns, identity, token_path=str(TOKEN_DIR / f'{identity}.token'), ca_path=CA_FILE)


def load_schedule(path):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('schema') != 'e42.schedule.v1':
        raise APIError('wrong E42 schedule schema')
    return doc, doc['trials']


def rec_base(s):
    return {'schema': 'e42.trial.v1', **s, 'status': 'COMPLETE', 'error': None,
            'semantic_validity': {'all_security_predicates_true_by_design': True,
                                  'benign_annotation': K.SEQ_ANN,
                                  'benign_annotation_in_V_core': False},
            'fixture': None, 'gate': None, 'delete': None, 'delete_preconditions': None,
            'post_action_validation': None,
            'writer_arrivals': [], 'arrivals_meta': None, 'effect': None}


def run_one(env, s):
    rec = rec_base(s)
    # Reuse E39 fixture/reset. E42 condition is not an invalidating condition, so fixture_values => all true.
    spec = {'run_id': s['run_id'], 'trial_id': s['trial_id'], 'stage': 1, 'block': s['block'],
            'experiment': 'B', 'arm': s['arm'], 'client_impl': 'python', 'action_type': 'pod_delete',
            'condition': COND_NOWRITE, 'delta_ms': s.get('delta_ms'), 'rep': s['rep'],
            'schedule_seed': s['schedule_seed'], 'lambda_per_s': None, 'injected_latency_ms': None,
            'acquisition_mode': None, 'planning_mode': None, 'guard_order': None, 'pair_id': None,
            'load_level': 'idle'}
    uid = R.reset_trial(env, spec, rec)
    writer = None
    if s['condition'] == COND_BENIGN:
        # A single distinct annotation update, scheduled relative to gate end.
        writer = ArrivalWriter(env.arrivals, K.SEQ_ANN, K.SEQ_ANN, str(s['trial_id']),
                               [float(s['delta_ms'])], window_ms=max(250.0, float(s['delta_ms']) + 100.0),
                               target_kind='pod_annotation', seq_key=None, lambda_per_s=None, pod_uid=uid).prearm()
    try:
        if s['arm'] == R.A4D:
            def after_gate(g):
                if writer is not None:
                    writer.arm(g['gate']['t_end_mono_ns'])
            out = AC.run_commit_bound(env.responder, R.A4D, uid, after_gate=after_gate)
            rec['gate'] = out['gate']
            rec['delete_preconditions'] = out['delete_preconditions']
            rec['delete'] = dict(out['delete'], conflict_kind=out.get('conflict_kind'))
            if rec['delete'].get('http_status') in (200, 202):
                rec['effect'] = R.verify_gone(env, uid, None)
        else:
            g = R.python_gate(env, R.A1, uid)
            rec['gate'] = g
            if writer is not None:
                writer.arm(g['t_end_mono_ns'])
            rec['delete_preconditions'] = {'uid': uid, 'resource_version': None}
            if g['decision'] == 'EXECUTE':
                d, ok, obj = R.python_delete(env, uid)
                rec['delete'] = d
                if ok:
                    rec['effect'] = R.verify_gone(env, uid, obj)
            else:
                rec['delete'] = R._not_dispatched(uid)
        # A 409 is called a false abort only if the target-resident security predicates remain true.
        pod_after, _ = env.responder.get_pod()
        if pod_after is None:
            rec['post_action_validation'] = {'pod_present': False, 'all_target_validity_annotations_true': None}
        else:
            md = pod_after.get('metadata') or {}
            anns = md.get('annotations') or {}
            vals = {cm: anns.get(K.CM_TO_ANN[cm]) for cm in K.POLICY_CMS}
            all_true = all(str(v).strip().lower() == 'true' for v in vals.values())
            rec['post_action_validation'] = {
                'pod_present': True, 'resource_version': md.get('resourceVersion'),
                'all_target_validity_annotations_true': all_true, 'target_validity_annotations': vals,
                'benign_seq_value': anns.get(K.SEQ_ANN)}
        if writer is not None:
            tdel = (rec.get('delete') or {}).get('t_resp_mono_ns') or (rec.get('gate') or {}).get('t_end_mono_ns')
            writer.halt(int(tdel))
            wout = writer.join()
            rec['writer_arrivals'] = (wout or {}).get('writer_arrivals', [])
            rec['arrivals_meta'] = (wout or {}).get('meta')
    finally:
        if writer is not None and writer.result is None:
            try:
                writer.cancel()
            except Exception:
                pass
    return rec


def run(args):
    doc, trials = load_schedule(args.schedule)
    if not args.lock:
        raise APIError('--lock is required for live E42 execution')
    verify_lock(args.lock, PKG, args.schedule, args.run_id, doc['schedule_seed'])
    if doc['run_id'] != args.run_id:
        raise APIError('schedule run_id mismatch')
    ns = R.NS_PREFIX + args.run_id
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / 'runner_e42.log', 'a', encoding='utf-8')
    env = R.Env(base=API_BASE, namespace=ns, run_id=args.run_id, seed=doc['schedule_seed'], out_dir=out,
                experiments=['B'], token_dir=TOKEN_DIR, ca_file=CA_FILE, logfh=log)
    raw = out / 'RAW_TRIALS_E42.jsonl'
    existing = set()
    if raw.exists():
        for line in raw.read_text(encoding='utf-8').splitlines():
            try:
                existing.add(int(json.loads(line)['trial_id']))
            except Exception:
                pass
    infra = done = 0
    try:
        st, _, ver = env.reset.get_version()
        gv = str((ver or {}).get('gitVersion', ''))
        if st != 200 or not gv.startswith(EXPECTED_VERSION_PREFIX):
            raise APIError(f'unexpected Kubernetes version {gv!r}')
        for s in trials:
            if int(s['trial_id']) in existing:
                continue
            try:
                rec = run_one(env, s)
            except Exception as exc:
                rec = rec_base(s)
                rec['status'] = 'INFRA_ERROR'
                rec['error'] = repr(exc)
                infra += 1
            with raw.open('a', encoding='utf-8') as f:
                f.write(json.dumps(rec, sort_keys=True) + '\n')
            done += 1
            if done >= 50 and infra / done > 0.05:
                raise APIError(f'E42 infrastructure error fraction {infra}/{done} exceeds 5%')
        stats = {'scheduled': len(trials), 'newly_written': done, 'infra_errors': infra,
                 'finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        (out / 'RUN_STATS_E42.json').write_text(json.dumps(stats, indent=2) + '\n', encoding='utf-8')
        return 0
    finally:
        env.close()
        log.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--make-schedule')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--reps', type=int)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--schedule')
    ap.add_argument('--output-dir')
    ap.add_argument('--lock')
    a = ap.parse_args(argv)
    if a.make_schedule:
        return make_schedule(a)
    if a.run:
        if not a.schedule or not a.output_dir:
            ap.error('--run requires --schedule and --output-dir')
        return run(a)
    ap.error('choose --make-schedule or --run')


if __name__ == '__main__':
    raise SystemExit(main())
