#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def rel(p): return p.relative_to(ROOT).as_posix()
def locked_files():
 files=[]
 for p in sorted((ROOT/'shared'/'E39_LOCKED_BASE').rglob('*')):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc': files.append(p)
 for base in ('A4D_CONFIRMATION','ops','01_ENVIRONMENT'):
  for p in sorted((ROOT/base).rglob('*')):
   if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc' and 'SCHEDULE_' not in p.name and not p.name.startswith('LOCK_'): files.append(p)
 files += [ROOT/'tools'/'lock_util.py']
 return {rel(p):sha(p) for p in sorted(set(files))}
def mk(mode,sched_rel,out_rel):
 sched=ROOT/sched_rel; d=json.loads(sched.read_text()); protocol=ROOT/'A4D_CONFIRMATION/protocol/A4D_CONFIRMATION_PROTOCOL.md'; plan=ROOT/'shared/E39_LOCKED_BASE/protocol/ANALYSIS_PLAN_E39.txt'
 lock={'schema':'a4d.confirmation.lock.v1','status':'LOCKED-PROSPECTIVE-R2-2026-10-01','experiment':'A4D_CONFIRMATION','mode':mode,'run_id':d['run_id'],'schedule_seed':d['schedule_seed'],'schedule_path':sched_rel,'schedule_sha256':sha(sched),'analysis_plan_path':rel(plan),'analysis_plan_sha256':sha(plan),'protocol_path':rel(protocol),'protocol_sha256':sha(protocol),'files':locked_files(),'note':'A4d-only R2 confirmation. Scientific outcomes never trigger stopping; audit/watch evidence integrity may fail/stop the run. R2 changes only the successful-DELETE RV evidence fallback and fresh run IDs.'}
 out=ROOT/out_rel; out.write_text(json.dumps(lock,indent=2,sort_keys=True)+'\n'); return out
locks=[mk('main','A4D_CONFIRMATION/protocol/SCHEDULE_A4D_MAIN.json','A4D_CONFIRMATION/protocol/LOCK_A4D_MAIN.json'),mk('smoke','A4D_CONFIRMATION/protocol/SCHEDULE_A4D_SMOKE.json','A4D_CONFIRMATION/protocol/LOCK_A4D_SMOKE.json')]
exclude_top={'runtime','.venv_runner','.venv_analysis','.venv_deploy','.pytest_cache'}; entries=[]
for p in sorted(ROOT.rglob('*')):
 if not p.is_file(): continue
 rr=p.relative_to(ROOT)
 if rr.parts and rr.parts[0] in exclude_top: continue
 if '__pycache__' in rr.parts or '.pytest_cache' in rr.parts or p.suffix=='.pyc' or rr.as_posix()=='FREEZE_MANIFEST.sha256': continue
 entries.append((sha(p),rr.as_posix()))
(ROOT/'FREEZE_MANIFEST.sha256').write_text(''.join(f'{h}  {r}\n' for h,r in entries),encoding='utf-8')
print(json.dumps({'locks':[rel(x) for x in locks],'freeze_files':len(entries),'freeze_sha256':sha(ROOT/'FREEZE_MANIFEST.sha256')},indent=2))
