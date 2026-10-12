#!/usr/bin/env python3
"""Regenerate experiment LOCKs and the package freeze manifest.
Run only before scientific main data, or after a documented pre-main smoke amendment.
"""
from __future__ import annotations
import hashlib,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def rel(p): return p.relative_to(ROOT).as_posix()

def locked_file_map(exp):
    files=[]
    # Entire inherited E39 scientific/deploy basis: avoids silently changing a transitive module.
    for p in sorted((ROOT/'shared'/'E39_LOCKED_BASE').rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc': files.append(p)
    files += [ROOT/'tools'/'lock_util.py']
    if exp=='E41':
        files += [ROOT/'E41_MULTIHOST_REPLICATION'/'scripts'/'e41_main.py',
                  ROOT/'E41_MULTIHOST_REPLICATION'/'protocol'/'E41_PROTOCOL.md']
    else:
        files += [ROOT/'E42_BENIGN_CHURN'/'scripts'/'e42_benign_churn.py',
                  ROOT/'E42_BENIGN_CHURN'/'scripts'/'analyze_e42.py',
                  ROOT/'E42_BENIGN_CHURN'/'protocol'/'E42_PROTOCOL.md']
    return {rel(p):sha(p) for p in sorted(set(files))}

def mk(exp,mode,sched_rel,out_rel):
    sched=ROOT/sched_rel; d=json.loads(sched.read_text())
    protocol_rel=f'{exp}_MULTIHOST_REPLICATION/protocol/E41_PROTOCOL.md' if exp=='E41' else 'E42_BENIGN_CHURN/protocol/E42_PROTOCOL.md'
    if exp=='E41': plan=ROOT/'shared'/'E39_LOCKED_BASE'/'protocol'/'ANALYSIS_PLAN_E39.txt'
    else: plan=ROOT/protocol_rel
    lock={'schema':f'{exp.lower()}.lock.v1','status':'LOCKED-PROSPECTIVE-2026-09-30',
          'experiment':exp,'mode':mode,'run_id':d['run_id'],'schedule_seed':d['schedule_seed'],
          'schedule_path':sched_rel,'schedule_sha256':sha(sched),
          'analysis_plan_path':rel(plan),'analysis_plan_sha256':sha(plan),
          'protocol_path':protocol_rel,'protocol_sha256':sha(ROOT/protocol_rel),
          'files':locked_file_map(exp),
          'note':'Scientific lock: no result-driven code/schedule/outcome changes. Smoke is separate and excluded from main analysis.'}
    out=ROOT/out_rel; out.write_text(json.dumps(lock,indent=2,sort_keys=True)+'\n')
    return out

locks=[
 mk('E41','main','E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_MAIN.json','E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_MAIN.json'),
 mk('E41','smoke','E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_SMOKE.json','E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_SMOKE.json'),
 mk('E42','main','E42_BENIGN_CHURN/protocol/SCHEDULE_E42_MAIN.json','E42_BENIGN_CHURN/protocol/LOCK_E42_MAIN.json'),
 mk('E42','smoke','E42_BENIGN_CHURN/protocol/SCHEDULE_E42_SMOKE.json','E42_BENIGN_CHURN/protocol/LOCK_E42_SMOKE.json')]
# Freeze all package-controlled source/protocol/schedule/operator files, but never runtime data or local virtualenvs.
exclude_top={'runtime','.venv_runner','.venv_analysis','.venv_deploy'}
entries=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file(): continue
    rr=p.relative_to(ROOT)
    if rr.parts and rr.parts[0] in exclude_top: continue
    if '__pycache__' in rr.parts or p.suffix=='.pyc' or rr.as_posix()=='FREEZE_MANIFEST.sha256': continue
    entries.append((sha(p),rr.as_posix()))
(ROOT/'FREEZE_MANIFEST.sha256').write_text(''.join(f'{h}  {r}\n' for h,r in entries),encoding='utf-8')
print(json.dumps({'locks':[rel(x) for x in locks],'freeze_files':len(entries),'freeze_sha256':sha(ROOT/'FREEZE_MANIFEST.sha256')},indent=2))
