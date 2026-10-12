#!/usr/bin/env python3
"""Offline replay/verification of Experiment 10 (A4d R2)."""
from pathlib import Path
import subprocess, sys, tempfile, json, hashlib, shutil
ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/Exp10_A4d_Direct_Binding_Confirmation'
CODE=ROOT/'code/Exp10_A4d_R2/frozen_execution'
MAINT=ROOT/'code/Exp10_A4d_R2/postrun_maintenance'
OUT=ROOT/'outputs/L2_A4D_R2'
OUT.mkdir(parents=True,exist_ok=True)

def sha(p):
    h=hashlib.sha256();
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def run(args):
    print('+',' '.join(map(str,args)))
    subprocess.run([str(x) for x in args],check=True)

def scientific_result(d):
    d=dict(d); d.pop('generated_utc',None); d.pop('inputs_sha256',None); return d

raw=EXP/'data/raw'; proc=EXP/'data/processed/analysis_a4d'; prot=EXP/'protocol'; res=EXP/'results'
with tempfile.TemporaryDirectory(prefix='a4d-r2-replay-') as td:
    td=Path(td); ana=td/'analysis'; ana.mkdir()
    run([sys.executable,CODE/'shared/E39_LOCKED_BASE/analysis/analyze_e39.py','--run-dir',raw,'--raw',raw/'RAW_TRIALS.jsonl','--webhook',raw/'webhook_log.jsonl','--audit',raw/'audit.log','--pod-events',raw/'pod_events.jsonl','--schedule',prot/'SCHEDULE_A4D_MAIN.json','--lock',prot/'LOCK_A4D_MAIN.json','--lock-root',CODE,'--analysis-plan',CODE/'shared/E39_LOCKED_BASE/protocol/ANALYSIS_PLAN_E39.txt','--stage','1','--experiments','A','--design-check','off','--out',ana,'--bootstrap-reps','2000'])
    summary=td/'A4D_CONFIRMATION_SUMMARY.json'
    evidence=td/'AUDIT_EVIDENCE_COMPLETENESS.json'
    run([sys.executable,MAINT/'A4D_CONFIRMATION/analysis/summarize_a4d.py','--analysis-dir',ana,'--out',summary])
    run([sys.executable,CODE/'A4D_CONFIRMATION/analysis/validate_audit_evidence.py','--raw',raw/'RAW_TRIALS.jsonl','--audit',raw/'audit.log','--pod-events',raw/'pod_events.jsonl','--expected','985','--out',evidence])
    checks=[]
    for n in ['cells_E39.csv','exposure_E39.csv','oracle_trials_E39.csv','stage2_candidates_E39.csv']:
        checks.append((n,sha(ana/n)==sha(proc/n)))
    checks.append(('A4D_CONFIRMATION_SUMMARY.json',sha(summary)==sha(res/'A4D_CONFIRMATION_SUMMARY.json')))
    checks.append(('AUDIT_EVIDENCE_COMPLETENESS.json',sha(evidence)==sha(res/'AUDIT_EVIDENCE_COMPLETENESS.json')))
    a=json.load(open(ana/'results_E39.json')); b=json.load(open(proc/'results_E39.json'))
    checks.append(('results_E39.json scientific keys',scientific_result(a)==scientific_result(b)))
    # Direct headline invariants.
    h=json.load(open(res/'HEADLINE_A4D_R2.json')); ev=json.load(open(res/'AUDIT_EVIDENCE_COMPLETENESS.json'))
    checks += [
      ('headline 985',h['completed_main_trials']==985),('async 0/936',h['async_trials']==936 and h['async_invalid_executed']==0),
      ('controls',h['no_write_trials']==39 and h['no_write_false_aborts']==0 and h['uid_rebind_controls']==10 and h['uid_rebind_blocked']==10),
      ('agreement 985/985',h['primary_secondary_exact_agreement']['agree']==985),
      ('evidence 1921/1921',ev['PASS'] and ev['critical_audit_records_required']==1921 and ev['critical_audit_records_validated']==1921 and ev['error_count']==0),
      ('RV fallback 958+17',ev['successful_delete_rv_sources']=={'audit':958,'pod_watch_deleted':17}),
    ]
    ok=all(v for _,v in checks)
    report=['Experiment 10 A4d R2 offline replay: '+('PASS' if ok else 'FAIL')]
    report += [('%-48s %s' % (n,'PASS' if v else 'FAIL')) for n,v in checks]
    (OUT/'L2_A4D_R2_REPORT.txt').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print('\n'.join(report))
    if not ok: raise SystemExit(1)
