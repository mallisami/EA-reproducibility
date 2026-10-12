#!/usr/bin/env python3
import argparse, hashlib, json, sys, time
from pathlib import Path

DET = Path(__file__).resolve().parents[1]
ROOT = DET.parent
sys.path.insert(0, str(DET))
from flashir_core.dcji import derive_critical_predicates

CFG_PATH = Path(__file__).resolve().with_name('E14_SUBFREEZE.json')
OUT_DIR = Path(__file__).resolve().with_name('results')
OUT_PATH = OUT_DIR / 'e14_deadline_discrimination.json'

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser(description='Run the frozen E14 deadline-discrimination stress test exactly once.')
    ap.add_argument('--execute', action='store_true', help='required to create the E14 result file')
    args = ap.parse_args()
    cfg = json.loads(CFG_PATH.read_text())
    scenario_rel = cfg['source_scenario']
    scenario_path = DET / scenario_rel
    locked = json.loads((DET / 'CONTROLLED_INPUTS_V8.sha256.json').read_text())
    expected_hash = locked.get(scenario_rel)
    actual_hash = sha256(scenario_path)
    if expected_hash != actual_hash:
        raise SystemExit(f'Frozen scenario hash mismatch: {scenario_rel}')
    scenario = json.loads(scenario_path.read_text())
    if not args.execute:
        print(json.dumps({
            'status': 'VALIDATED_NOT_RUN',
            'freeze_id': cfg['freeze_id'],
            'scenario': scenario['scenario_id'],
            'scenario_hash_verified': True,
            'next_action': 'Re-run with --execute exactly once to generate E14 output.'
        }, indent=2))
        return 0
    OUT_DIR.mkdir(exist_ok=True)
    if OUT_PATH.exists():
        raise SystemExit('E14 result already exists; refusing overwrite')
    rows = []
    for label, deadline in [
        ('short_deadline', cfg['conditions']['short_deadline_s']),
        ('deadline_constrained', cfg['conditions']['primary_deadline_s']),
        ('no_deadline_proxy', cfg['conditions']['no_deadline_proxy_horizon_s'])
    ]:
        t0 = time.perf_counter()
        plan = derive_critical_predicates(
            scenario, float(deadline), cfg['attacker_capability'], cfg['profile']
        )
        runtime_ms = (time.perf_counter() - t0) * 1000.0
        rows.append({
            'condition': label,
            'deadline_s': float(deadline),
            'status': plan['status'],
            'goal_count': plan.get('goal_count'),
            'critical_query_ids': plan.get('critical_query_ids'),
            'query_cost_units': plan.get('query_cost_units'),
            'expanded': plan.get('expanded'),
            'subset_evals': plan.get('subset_evals'),
            'planning_runtime_ms': runtime_ms
        })
    rec = {
        'experiment': 'E14',
        'freeze_id': cfg['freeze_id'],
        'classification': cfg['classification'],
        'scenario_id': scenario['scenario_id'],
        'scenario_sha256': actual_hash,
        'generated_at_unix': time.time(),
        'conditions': rows,
        'claim_boundary': 'Mechanistic deadline-discrimination result only; does not modify or extend the locked held-out confirmatory population.'
    }
    OUT_PATH.write_text(json.dumps(rec, indent=2) + '\n')
    print(json.dumps(rec, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
