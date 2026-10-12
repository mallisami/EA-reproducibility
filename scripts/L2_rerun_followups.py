#!/usr/bin/env python3
"""Offline replay of E41/E42 from public-safe raw archives; byte-compare canonical outputs."""
from pathlib import Path
import subprocess,sys,tempfile,tarfile,hashlib,shutil,json
ROOT=Path(__file__).resolve().parent.parent
CODE=ROOT/'code'/'Exp8_E41_E42_Azure_2VM_Followup'
E41=ROOT/'experiments'/'Exp8_E41_Azure_2VM_MultiHost'
E42=ROOT/'experiments'/'Exp9_E42_Benign_Target_Churn'
OUT=ROOT/'outputs'/'L2_FOLLOWUPS'; OUT.mkdir(parents=True,exist_ok=True)

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def run(args): subprocess.run([str(x) for x in args],check=True)
def cmp(a,b): return a.is_file() and b.is_file() and sha(a)==sha(b)
checks=[]
with tempfile.TemporaryDirectory(prefix='ea_followup_') as td:
 td=Path(td); e41x=td/'e41'; e42x=td/'e42'; e41x.mkdir(); e42x.mkdir()
 with tarfile.open(E41/'data/raw/E41_RUNTIME_PUBLIC_REDACTED.tar.gz','r:gz') as t: t.extractall(e41x, filter='data')
 with tarfile.open(E42/'data/raw/E42_RUNTIME_PUBLIC_REDACTED.tar.gz','r:gz') as t: t.extractall(e42x, filter='data')
 # E41 frozen primary analysis
 r41=e41x/'e41r-20260930'/'results'; o41=td/'e41_out'
 run([sys.executable,CODE/'shared/E39_LOCKED_BASE/analysis/analyze_e39.py','--run-dir',r41,
      '--raw',r41/'RAW_TRIALS.jsonl','--webhook',r41/'webhook_log.jsonl','--audit',r41/'audit.log',
      '--pod-events',r41/'pod_events.jsonl','--schedule',CODE/'E41_MULTIHOST_REPLICATION/protocol/SCHEDULE_E41_MAIN.json',
      '--lock',CODE/'E41_MULTIHOST_REPLICATION/protocol/LOCK_E41_MAIN.json','--lock-root',CODE,
      '--analysis-plan',CODE/'shared/E39_LOCKED_BASE/protocol/ANALYSIS_PLAN_E39.txt','--stage','1','--experiments','A',
      '--design-check','off','--out',o41,'--bootstrap-reps','2000'])
 sum41=td/'E41_SUMMARY.json'
 run([sys.executable,CODE/'E41_MULTIHOST_REPLICATION/analysis/summarize_e41.py','--analysis-dir',o41,'--out',sum41])
 sens=td/'sens'; sens.mkdir()
 run([sys.executable,CODE/'E41_MULTIHOST_REPLICATION/analysis/E41_recovery_sensitivity_recompute.py','--results',r41,'--outdir',sens])
 for fn in ['cells_E39.csv','exposure_E39.csv','oracle_trials_E39.csv','results_E39.json','stage2_candidates_E39.csv']:
  # results_E39 contains machine path/time provenance; compare result keys for JSON, byte compare others.
  if fn=='results_E39.json':
   a=json.loads((o41/fn).read_text()); b=json.loads((E41/'data/processed'/fn).read_text())
   for x in (a,b): x.pop('generated_utc',None); x.pop('inputs_sha256',None)
   checks.append((f'E41 {fn} scientific keys',a==b))
  else: checks.append((f'E41 {fn}',cmp(o41/fn,E41/'data/processed'/fn)))
 checks.append(('E41 summary',cmp(sum41,E41/'data/processed/E41_SUMMARY.json')))
 checks.append(('E41 sensitivity CSV',cmp(sens/'E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.csv',E41/'data/processed/E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.csv')))
 checks.append(('E41 sensitivity JSON',cmp(sens/'E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.json',E41/'data/processed/E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.json')))
 # E42
 r42=e42x/'e42b-20260930'/'results'; o42=td/'e42_out'
 run([sys.executable,CODE/'E42_BENIGN_CHURN/scripts/analyze_e42.py','--raw',r42/'RAW_TRIALS_E42.jsonl','--out',o42])
 checks.append(('E42 cells',cmp(o42/'cells_E42.csv',E42/'data/processed/cells_E42.csv')))
 checks.append(('E42 summary',cmp(o42/'summary_E42.json',E42/'data/processed/summary_E42.json')))

ok=all(x for _,x in checks)
rep=OUT/'L2_FOLLOWUPS_REPORT.txt'
rep.write_text('L2 follow-ups '+('PASS' if ok else 'FAIL')+f': {sum(x for _,x in checks)}/{len(checks)} checks\n'+''.join(f'{"PASS" if x else "FAIL"}\t{n}\n' for n,x in checks),encoding='utf-8')
print(rep.read_text())
sys.exit(0 if ok else 1)
