#!/usr/bin/env python3
"""Regenerate E43-E50 analysis outputs from distributed trial records.

For each frozen run, copy only trials.jsonl to a temporary directory, execute the frozen
analysis program, and require regenerated summary.json and cells.csv to be byte-identical
to the distributed outputs. Canonical experiment files are never overwritten.
"""
from pathlib import Path
import hashlib, shutil, subprocess, sys, tempfile

ROOT=Path(__file__).resolve().parents[1]
EX=ROOT/'experiments'
CODE=ROOT/'code'/'Exp11-16_Scope_Strengthening_E43_E50'
OUT=ROOT/'outputs'/'L2_SCOPE_REGEN'
OUT.mkdir(parents=True,exist_ok=True)

runs=[
 ('Exp11_E43_Grace_Period_Sensitivity/E43_serial_check','ea_exp.py'),
 ('Exp11_E43_Grace_Period_Sensitivity/E43','ea_exp.py'),
 ('Exp12_E46_Target_State_Admission_vs_RV_Binding/E46','ea_exp2.py'),
 ('Exp13_E47_Retry_Policy/E47','ea_exp2.py'),
 ('Exp14_E48_Quarantine_PATCH/E48','ea_exp2.py'),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep1','ea_exp2.py'),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep2','ea_exp2.py'),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep3','ea_exp2.py'),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep4','ea_exp2.py'),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep5','ea_exp2.py'),
 ('Exp16_E50_Replicated_Control_Plane_Topology/E50_ha','ea_exp2.py'),
]

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

checks=[]
with tempfile.TemporaryDirectory(prefix='ea_scope_regen_') as td:
    td=Path(td)
    for i,(rel,analyzer) in enumerate(runs,1):
        src=EX/rel; work=td/f'run_{i:02d}'; work.mkdir()
        shutil.copy2(src/'trials.jsonl',work/'trials.jsonl')
        cp=subprocess.run([sys.executable,str(CODE/analyzer),'analyze','--out',str(work)],cwd=CODE,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        if cp.returncode!=0:
            checks.append((rel,'analysis execution',False,cp.stdout[-1000:]))
            continue
        for name in ('summary.json','cells.csv'):
            got=work/name; exp=src/name
            ok=got.is_file() and exp.is_file() and got.read_bytes()==exp.read_bytes()
            checks.append((rel,name,ok,f'expected={sha(exp) if exp.is_file() else "missing"}; regenerated={sha(got) if got.is_file() else "missing"}'))

report=OUT/'L2_SCOPE_REGEN_REPORT.txt'
passed=sum(1 for _,_,ok,_ in checks if ok)
with report.open('w',encoding='utf-8') as f:
    f.write(f'L2_SCOPE_REGEN {"PASS" if passed==len(checks) else "FAIL"}: {passed}/{len(checks)} byte-identity checks\n')
    for rel,name,ok,detail in checks:
        f.write(f'{"PASS" if ok else "FAIL"}\t{rel}\t{name}\t{detail}\n')
print(report.read_text(encoding='utf-8'))
sys.exit(0 if passed==len(checks) else 1)
