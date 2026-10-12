#!/usr/bin/env python3
"""E41 focused multi-host replication wrapper over the locked E39 harness.

The scientific change is deliberately minimal:
  * deployment changes from single-node kind to two-host K3s v1.37.x;
  * target Pods and the admission webhook are scheduled on the worker host;
  * the E39-A core design is reduced prospectively to A1/A3/A4d, all three
    invalidating conditions, and the eight-point {0,2,5,10,20,50,100,200} ms grid;
  * no adaptive stage-2 top-up is used.

Gate, writer, DELETE, record schema, audit-based oracle inputs, and all mechanism
implementations come from shared/E39_LOCKED_BASE without substantive alteration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PKG = HERE.parents[2]
BASE = PKG / 'shared' / 'E39_LOCKED_BASE'
SRC = BASE / 'src'
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PKG / 'tools'))

import e39_kube as K  # noqa: E402
import e39_runner as R  # noqa: E402
from e39_kube import APIError, E39Client  # noqa: E402
from lock_util import verify_lock  # noqa: E402

API_BASE = os.environ.get('E41_API_SERVER', 'https://127.0.0.1:6443')
CA_FILE = os.environ.get('E41_CA_FILE', '/var/lib/rancher/k3s/server/tls/server-ca.crt')
TOKEN_DIR = Path(os.environ.get('E41_TOKEN_DIR', str(PKG / 'runtime' / 'tokens')))
WORKER_LABEL_KEY = os.environ.get('E41_WORKER_LABEL_KEY', 'e41-role')
WORKER_LABEL_VALUE = os.environ.get('E41_WORKER_LABEL_VALUE', 'workload')
EXPECTED_VERSION_PREFIX = os.environ.get('E41_K8S_PREFIX', 'v1.37')

# Prospective E41 subset. These globals are read dynamically by build_stage1.
R.ARMS_A = (R.A1, R.A3, R.A4D)
R.ASYNC = ('async_scope', 'async_app_policy', 'async_evidence_last_literal')
R.DELTAS_A = R.DELTAS_SHORT
R.FORBIDDEN_SEEDS = tuple(R.FORBIDDEN_SEEDS) + (40926017, 972231175)
# Two VMs are expected to have >=4 GiB each. Guard the control/driver host conservatively.
R.MIN_MEM_AVAILABLE_KB = 768 * 1024
R.ABORT_MEM_AVAILABLE_KB = 512 * 1024

# Preserve E39 API safety rail (https://127.0.0.1:PORT only), but force target Pods to the worker
# and permit normal registry pulling on the fresh worker node.
def _create_target_pod_multihost(self, annotations=None, extra_labels=None, webhook=False,
                                 image='registry.k8s.io/pause:3.10'):
    labels = {'app': 'e39', 'e39-managed': 'true'}
    labels.update(extra_labels or {})
    if webhook:
        labels[K.WEBHOOK_LABEL[0]] = K.WEBHOOK_LABEL[1]
    md = {'name': self.pod_name, 'namespace': self.ns, 'labels': labels}
    if annotations:
        md['annotations'] = {k: str(v) for k, v in annotations.items()}
    body = {
        'apiVersion': 'v1', 'kind': 'Pod', 'metadata': md,
        'spec': {
            'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 0,
            'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'nodeSelector': {WORKER_LABEL_KEY: WORKER_LABEL_VALUE},
            'containers': [{'name': 'target', 'image': image, 'imagePullPolicy': 'IfNotPresent'}],
        },
    }
    return self.request('POST', f'{self._ns_root()}/pods', body)

K.E39Client.create_target_pod = _create_target_pod_multihost


def sha256_file(p):
    return R.sha256_file(p)


def make_schedule(args):
    if args.seed is None:
        raise SystemExit('--seed is required')
    R.check_seed(args.seed)
    trials = R.build_stage1(args.seed, args.run_id, reps=args.reps, experiments=('A',))
    note = ('E41 focused multi-host replication: A1/A3/A4d x three E39 invalidating conditions x '
            '{0,2,5,10,20,50,100,200} ms; no_write 39/arm; uid_rebind_control 10/arm; no stage-2 top-up')
    if args.reps is not None:
        note += f'; reps={args.reps} smoke only'
    doc = R.schedule_document(trials, 1, args.seed, args.run_id, note=note)
    out = Path(args.make_schedule)
    if out.exists():
        raise SystemExit(f'refusing to overwrite {out}')
    out.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'n_trials': len(trials), 'blocks': len({t['block'] for t in trials}),
                      'sha256': sha256_file(out)}, indent=1))
    return 0


def mk_client(identity, namespace):
    return E39Client(API_BASE, namespace, identity,
                     token_path=str(TOKEN_DIR / f'{identity}.token'), ca_path=CA_FILE)


def identity_probe(namespace):
    out = {}
    for ident in ('responder', 'writer', 'reset', 'mirror'):
        c = mk_client(ident, namespace)
        try:
            out[ident] = c.whoami()
        finally:
            c.close()
    want = {i: R.SA_PREFIX + i for i in out}
    bad = {i: {'got': out[i], 'want': want[i]} for i in out if out[i] != want[i]}
    if bad:
        raise APIError('identity mismatch: ' + json.dumps(bad))
    return out


def run(args):
    doc, specs = R.load_schedule_file(args.schedule)
    if not args.lock:
        raise APIError('--lock is required for live E41 execution')
    verify_lock(args.lock, PKG, args.schedule, args.run_id, doc['schedule_seed'])
    if doc['run_id'] != args.run_id:
        raise APIError('schedule run_id does not match --run-id')
    ns = R.NS_PREFIX + args.run_id
    K.check_namespace(ns)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    logfh = open(out / 'runner.log', 'a', encoding='utf-8')
    who = identity_probe(ns)
    env = R.Env(base=API_BASE, namespace=ns, run_id=args.run_id, seed=doc['schedule_seed'], out_dir=out,
                experiments=['A'], token_dir=TOKEN_DIR, ca_file=CA_FILE, logfh=logfh)
    try:
        st, _, ver = env.reset.get_version()
        gv = str((ver or {}).get('gitVersion', ''))
        if st != 200 or not gv.startswith(EXPECTED_VERSION_PREFIX):
            raise APIError(f'unexpected Kubernetes version {gv!r}; expected {EXPECTED_VERSION_PREFIX}*')
        session = {
            'schema': 'e41.session.v1', 'run_id': args.run_id, 'schedule_seed': doc['schedule_seed'],
            'schedule_sha256': sha256_file(args.schedule), 'n_scheduled': len(specs),
            'api_server': API_BASE, 'ca_sha256': sha256_file(CA_FILE), 'kubernetes_version': gv,
            'deployment': 'two-host K3s; driver/control-plane on server; target+webhook pinned to worker',
            'worker_selector': {WORKER_LABEL_KEY: WORKER_LABEL_VALUE}, 'identities': who,
            'design': {'arms': list(R.ARMS_A), 'conditions': list(R.ASYNC), 'deltas_ms': list(R.DELTAS_A),
                       'stage2_topup': False},
            'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        (out / 'SESSION_E41.json').write_text(json.dumps(session, indent=2) + '\n', encoding='utf-8')
        raw = out / 'RAW_TRIALS.jsonl'
        stats = R.run_trials(env, specs, raw, logfh=logfh, guards=True, stop_file=out / 'STOP')
        (out / 'RUN_STATS_E41.json').write_text(json.dumps(stats, indent=2) + '\n', encoding='utf-8')
        return 0 if not stats.get('aborted') else 3
    finally:
        env.close()
        logfh.close()


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
