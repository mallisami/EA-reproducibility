#!/usr/bin/env python3
"""Portable replay of the designed-state results and post-hoc T4/T5/TTL analyses.

The scientific sources are copied, not edited. Historical paths are resolved in
the child interpreter to a temporary workspace. Runtime fields are excluded from
deterministic result comparisons, as in the supplied comparison utility.
"""
import argparse, csv, json, os, runpy, shutil, subprocess, sys, tempfile
from pathlib import Path
from L2_rerun_analysis import compare_csv
ROOT=Path(__file__).resolve().parents[1]
CODE=ROOT/'code/Exp1_offline_reverification'
EX=ROOT/'experiments/Exp1_Designed_State_Verification'
OUT=ROOT/'outputs/deterministic'

def semantic(x):
    if isinstance(x,dict):return {k:semantic(v) for k,v in x.items() if 'runtime' not in k and not k.startswith('_')}
    if isinstance(x,list):return [semantic(v) for v in x]
    return x
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--steps',default='regression,minimality,ttl');args=ap.parse_args()
    steps=set(args.steps.split(','));unknown=steps-{'regression','minimality','ttl'}
    if unknown:raise ValueError('Unknown steps: '+','.join(unknown))
    OUT.mkdir(parents=True,exist_ok=True);checks=[]
    def check(name,ok):
        checks.append((name,ok));print(('PASS' if ok else 'FAIL')+': '+name,flush=True)
    def execute(cmd,cwd,env,log_name):
        with (OUT/log_name).open('w') as f:
            p=subprocess.run(cmd,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
        if p.returncode:raise RuntimeError(f'{log_name}: command exited {p.returncode}; see {OUT/log_name}')
    with tempfile.TemporaryDirectory(prefix='discover_deterministic_') as td:
        w=Path(td);work=w/'work/offline';work.mkdir(parents=True)
        shutil.copytree(CODE/'work_offline',work,dirs_exist_ok=True)
        shutil.copytree(CODE/'scripts',work/'scripts',dirs_exist_ok=True)
        f8=w/'inputs/frozen/02_locked_methodology_v8.1.1/deterministic'
        shutil.copytree(ROOT/'code/Exp1_deterministic_benchmark_locked',f8)
        (f8/'results/heldout').mkdir(parents=True)
        shutil.copy2(EX/'data/raw/E5_heldout/e5_defense_comparison.json',f8/'results/heldout')
        f9=w/'inputs/frozen/03_extended_study_v9'
        shutil.copytree(f8/'followup_W2_scenarios',f9/'scenarios/pilot')
        (f9/'results').mkdir(parents=True)
        shutil.copy2(EX/'data/raw/E5_followup_W2/pilot_confirmatory_W2.json',f9/'results')
        env=dict(os.environ, PYTHONHASHSEED='321')
        if 'regression' in steps:
            expected=json.loads((EX/'data/raw/E5_heldout/e5_defense_comparison.json').read_text())
            pilot=json.loads((f9/'results/pilot_confirmatory_W2.json').read_text())
            for variant in ['orig','patched']:
                core=work/f'deterministic_{variant}';output=work/f'regression_{variant}';output.mkdir()
                runenv=dict(env,DCJI_OUT=str(output))
                execute([sys.executable,'run_heldout_e1_e5.py'],core,runenv,f'{variant}_heldout.log')
                actual=json.loads((output/'e5_defense_comparison.json').read_text())
                check(f'{variant} E5 rows, decisions, costs, certificates',semantic(actual)==semantic(expected))
                runenv.update(DCJI_CORE_ROOT=str(core),DCJI_PILOT_DIR=str(f9/'scenarios/pilot'),DCJI_OUT_FILE=str(output/'pilot_confirmatory_W2.json'))
                execute([sys.executable,str(work/'scripts/run_pilot_w2_redirect.py')],core,runenv,f'{variant}_pilot.log')
                actual=json.loads((output/'pilot_confirmatory_W2.json').read_text())
                check(f'{variant} 68 follow-up W2 specifications',semantic(actual)==semantic(pilot))
                shutil.copytree(output,OUT/variant,dirs_exist_ok=True)
        for step,script,directory in [('minimality','analysis_t4_t5.py','t4_t5'),('ttl','ttl_sweep.py','t6_ttl')]:
            if step not in steps:continue
            target=work/'scripts'/script
            # Resolve only the historical workspace path; keep analysis code unmodified.
            bootstrap=("import os,runpy; old=os.path.expanduser; "
                       "os.path.expanduser=lambda p:%r if p=='~/dcji_reanalysis_20260926' else old(p); "
                       "runpy.run_path(%r,run_name='__main__')")%(str(w),str(target))
            execute([sys.executable,'-c',bootstrap],work,env,step+'.log')
            generated=w/'out/offline'/directory;canonical=EX/'data/processed/offline_reverification'/directory
            shutil.copytree(generated,OUT/directory,dirs_exist_ok=True)
            for p in sorted(canonical.iterdir()):
                q=generated/p.name
                if p.name=='t4_minimality_full_universe.csv':
                    # 'secs' is the measured execution time, not a scientific outcome.
                    def read_semantic_csv(path):
                        with path.open(newline='') as f:
                            return [{k:v for k,v in r.items() if k!='secs'} for r in csv.DictReader(f)]
                    ok=read_semantic_csv(q)==read_semantic_csv(p)
                elif p.suffix=='.csv':ok=compare_csv(q,p)[0] in ['IDENTICAL','NUMERICALLY_EQUAL']
                elif p.suffix=='.json':ok=json.loads(q.read_text())==json.loads(p.read_text())
                else:ok=q.read_bytes()==p.read_bytes()
                check(step+' '+p.name,ok)
    report={'steps':sorted(steps),'passed':sum(ok for _,ok in checks),'checks':len(checks),
            'failed':[name for name,ok in checks if not ok],'runtime_values_compared':False,
            'excluded_timing_columns':{'t4_minimality_full_universe.csv':['secs']}}
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    return 1 if report['failed'] else 0
if __name__=='__main__':sys.exit(main())
