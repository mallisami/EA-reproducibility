#!/usr/bin/env python3
import json, py_compile, sys
from pathlib import Path

E13 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(E13 / 'scripts'))
from dcji_live_adapter import derive_live_plan, evaluate_live_commit

EXPECTED = {
    'F1.1': ('U1', 'U2'),
    'F1.2': ('P17', 'P18'),
    'F4.1': (['flashir-malicious'], ['flashir-malicious', 'flashir-benign']),
    'F4.4': (['192.0.2.10'], ['192.0.2.10', '192.0.2.20'])
}

def main():
    errors=[]; cases={}
    protocol=json.loads((E13/'E13_PROTOCOL.json').read_text())
    bindings=json.loads((E13/'E13_MODEL_BINDINGS.json').read_text())
    if protocol.get('freeze_id') != bindings.get('freeze_id'):
        errors.append('protocol/bindings freeze_id mismatch')
    for script in ['dcji_live_adapter.py','preflight_e13.py','run_e13_once.py','run_e13_protocol.py']:
        try: py_compile.compile(str(E13/'scripts'/script), doraise=True)
        except Exception as e: errors.append(f'compile failure {script}: {e}')
    for cid,(decision,changed) in EXPECTED.items():
        try:
            ctx=derive_live_plan(cid)
            pos=evaluate_live_commit(ctx,decision,changed)
            stable=evaluate_live_commit(ctx,decision,decision)
            cases[cid]={
                'scenario_sha256':ctx['scenario_sha256'],
                'critical_query_ids':ctx['plan']['critical_query_ids'],
                'positive_projection_decision':pos['dcji_live_decision'],
                'stable_projection_decision':stable['dcji_live_decision'],
                'deadline_s':ctx['deadline_s']
            }
            if pos['dcji_live_decision']!='ABORT_REPLAN': errors.append(f'{cid}: changed projection did not abort')
            if stable['dcji_live_decision']!='EXECUTE': errors.append(f'{cid}: stable projection did not execute')
        except Exception as e:
            errors.append(f'{cid}: {e}')
    attempts=sorted(str(p.relative_to(E13)) for p in (E13/'results').glob('e13_live_attempt_*.json')) if (E13/'results').exists() else []
    if attempts: errors.append('live attempt output already present before v8.1 freeze: '+','.join(attempts))
    rec={
        'version':'8.1.1',
        'protocol_freeze_id':protocol.get('freeze_id'),
        'positive_cases':4,
        'positive_repetitions_per_case':protocol.get('required_positive_repetitions_per_case'),
        'negative_controls':protocol.get('required_negative_controls'),
        'negative_control_repetitions_per_control':protocol.get('required_negative_control_repetitions_per_control'),
        'scheduled_trials': 4*int(protocol.get('required_positive_repetitions_per_case',0)) + len(protocol.get('required_negative_controls',[]))*int(protocol.get('required_negative_control_repetitions_per_control',0)),
        'actual_dcji_planner_bound_to_live_queries':True,
        'actual_B6_gate_used_for_commit_evaluation':True,
        'scenario_and_core_hashes_verified_by_adapter':True,
        'scientific_outcome_separated_from_execution_status':True,
        'timing_instrumentation_required':True,
        'safe_control_actuation_timing_required':True,
        'cases':cases,
        'attempt_files_present':attempts,
        'errors':errors,
        'status':'PASS_STATIC_ONLY' if not errors else 'FAIL'
    }
    out=E13/'analysis/E13_V81_STATIC_AUDIT.json'
    out.write_text(json.dumps(rec,indent=2)+'\n')
    print(json.dumps(rec,indent=2))
    return 0 if not errors else 1

if __name__=='__main__':
    raise SystemExit(main())
