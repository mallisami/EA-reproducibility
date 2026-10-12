#!/usr/bin/env python3
"""Offline frozen-planner check. Pure Python; imports copied frozen src only (no kubernetes/network)."""
import hashlib, json, sys, time, statistics as st, platform
from pathlib import Path
BASE = Path.home() / 'dcji_reanalysis_20260926'
ROOT = BASE / 'work/e38/planner/EXECUTE_ON_AZURE'
HERE = ROOT / 'src'
OUT = BASE / 'out/e38'
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def source_tree_sha():
    h = hashlib.sha256()
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.json'):
            h.update(str(path.relative_to(HERE)).encode() + b'\0' + sha(path).encode() + b'\n')
    prov = ROOT / 'source_provenance/E37_F5_PHYS_ORIGINAL.json'
    h.update(b'source_provenance/E37_F5_PHYS_ORIGINAL.json\0' + sha(prov).encode() + b'\n')
    return h.hexdigest()

session = json.load(open(BASE / 'inputs/e38raw/SESSION.json'))
res = {'provenance': {
    'source_tree_sha256_recomputed': source_tree_sha(), 'source_tree_sha256_session': session['source_tree_sha256'],
    'model_sha256_recomputed': sha(HERE / 'e38_model.py'), 'model_sha256_session': session['model_sha256'],
    'runner_sha256_recomputed': sha(HERE / 'e38_runner.py'), 'runner_sha256_session': session['source_sha256'],
    'provenance_json_sha256': sha(ROOT / 'source_provenance/E37_F5_PHYS_ORIGINAL.json')}}
for k in ('source_tree', 'model', 'runner'):
    res['provenance'][k + '_match'] = res['provenance'][k + '_sha256_recomputed'] == res['provenance'][k + '_sha256_session']

import e38_model
from flashir_core.dcji import derive_critical_predicates
from e38_independent_offline_oracle import State, asymmetric_goals, symmetric_goals, enumerate_exact_minimum

model = e38_model.construct_model()
res['transitions'] = [dict(id=t['transition_id'], actor=t.get('actor'), tier=t.get('capability_tier'),
                           duration_s=t.get('duration_s'), cost=t.get('cost_ops')) for t in model['transitions']]
res['horizons'] = {}
for H in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0):
    r = derive_critical_predicates(e38_model.construct_model(), H, 'C2', 'balanced')
    sel = sorted(e38_model.PREDICATE_TO_PHYSICAL[p] for p in (r.get('critical_predicates') or []))
    res['horizons'][str(H)] = dict(status=r['status'], goal_count=r['goal_count'], expanded=r['expanded'],
        subset_evals=r['subset_evals'], critical_predicates=r.get('critical_predicates'), certificate=sel,
        query_cost_units=r.get('query_cost_units'), candidate_query_ids=r.get('candidate_query_ids'))

# timing: median of 20 runs
def timeit(fn, n=20):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter_ns(); fn(); xs.append((time.perf_counter_ns() - t0) / 1e6)
    return dict(n=n, median_ms=st.median(xs), min_ms=min(xs), max_ms=max(xs), mean_ms=st.mean(xs))
m5 = e38_model.construct_model()
res['timing'] = {
    'derive_critical_predicates_H5_ms': timeit(lambda: derive_critical_predicates(e38_model.construct_model(), 5.0, 'C2', 'balanced')),
    'derive_critical_predicates_H30_ms': timeit(lambda: derive_critical_predicates(e38_model.construct_model(), 30.0, 'C2', 'balanced')),
    'frozen_planner_certificate_end_to_end_ms(H5,incl model build+independent oracle)': timeit(e38_model.frozen_planner_certificate),
    'independent_exact_minimum_ms': timeit(lambda: enumerate_exact_minimum(State(), asymmetric_goals())),
    'host': platform.platform(), 'python': sys.version.split()[0]}
fc = e38_model.frozen_planner_certificate()
res['frozen_planner_certificate'] = dict(observations=fc['observations'], goal_count=fc['plan']['goal_count'],
                                         independent_exact_minima=fc['independent_exact_minima'])
res['independent_minimum_asymmetric'] = [list(x) for x in enumerate_exact_minimum(State(), asymmetric_goals())]
res['independent_minimum_symmetric'] = [list(x) for x in enumerate_exact_minimum(State(), symmetric_goals())]
json.dump(res, open(OUT / 'E38_PLANNER_CHECK.json', 'w'), indent=2, default=str)
print(json.dumps(res, indent=1, default=str))
