#!/usr/bin/env python3
"""Build protocol/LOCK_E40.json (E40 protocol section 7). usage: make_lock_e40.py SCHEDULE RUN_ID E39_LOCK_PATH E39_SEED"""
import glob
import hashlib
import json
import os
import sys
import time

W = os.path.expanduser('~/e40_20260926')


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def main():
    sched, run_id, e39lock, e39seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    out = os.path.join(W, 'protocol', 'LOCK_E40.json')
    if os.path.exists(out):
        raise SystemExit('refusing to overwrite ' + out)
    pats = ['code/src/*.py', 'code/src/flashir_core/*.py', 'code/frozen_ref/source_provenance/*.json', 'code/webhook/*.py',
            'code/deploy/*.yaml', 'code/deploy/*.sh', 'code/analysis/*.py', 'code/protocol/ANALYSIS_PLAN_E39.txt',
            'e40/*.py', 'k8s/runner-job.yaml', 'e40_ops.sh', 'pylib.tgz', 'protocol/E40_PROTOCOL.txt',
            'protocol/COPY_SHA256_E39.txt', 'protocol/RENAME_E39_TO_E40.diff', 'render/%s/*.yaml' % run_id]
    files = {}
    for p in pats:
        for f in sorted(glob.glob(os.path.join(W, p))):
            files[os.path.relpath(f, W)] = sha(f)
    inc = {}
    for f in sorted(glob.glob(os.path.join(W, 'code/src/*.py'))):
        inc['src/' + os.path.basename(f)] = files[os.path.relpath(f, W)]
    for f in sorted(glob.glob(os.path.join(W, 'code/src/flashir_core/*.py'))):
        inc['src/flashir_core/' + os.path.basename(f)] = files[os.path.relpath(f, W)]
    inc['frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json'] = files['code/frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json']
    for n in ('e40_main.py', 'e40_podwatch.py'):
        inc['e40/' + n] = files['e40/' + n]
    doc = json.load(open(sched))
    lock = {'schema': 'e40.lock.v1', 'status': 'LOCKED', 'experiment': 'E40',
            'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'run_id': run_id,
            'schedule_path': os.path.relpath(sched, W), 'schedule_sha256': sha(sched),
            'schedule_seed': doc['schedule_seed'], 'n_trials': doc['n_trials'],
            'analysis_plan_path': 'code/protocol/ANALYSIS_PLAN_E39.txt',
            'analysis_plan_sha256': files['code/protocol/ANALYSIS_PLAN_E39.txt'],
            'e39_lock_path': e39lock, 'e39_lock_sha256': sha(e39lock), 'e39_schedule_seed': e39seed,
            'design': {'schedule_seed': doc['schedule_seed'],
                       'forbidden_schedule_seeds': [20260912, 1772720255, 154080643, 424242, e39seed],
                       'arms': ['A1_dcji', 'A3_dcji_webhook', 'A4d_commit_direct'],
                       'conditions': ['async_scope', 'async_evidence_last_literal'],
                       'delta_grid_ms': [0, 2, 5, 10, 20, 50, 100, 200], 'n_per_cell': 39,
                       'controls': {'no_write': 39, 'uid_rebind_control': 10}, 'total_trials': 2019,
                       'time_box': 'whole blocks; stop before a block if cluster age + projected block time > 2h40m',
                       'oracle': 'E39 oracle_e39.py with empty audit log (client RVs + pod-watch DELETED fallback); '
                                 'secondary = client timestamps (e40_supplement.py)'},
            'files': files, 'incluster_files': inc}
    json.dump(lock, open(out, 'w'), indent=1, sort_keys=True)
    print(out, sha(out), len(files), 'files', len(inc), 'in-cluster files')


if __name__ == '__main__':
    main()
