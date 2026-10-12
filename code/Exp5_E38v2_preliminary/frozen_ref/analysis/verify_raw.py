#!/usr/bin/env python3
"""Inspect EVERY captured physical trial. Report flaws; never filter the dataset."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

def audit(rows,session=None):
    warnings=[];errors=[]
    if session is not None:
        import collections
        expected={(c,m,b,r) for c in session['conditions'] for m in session['methods']
                  for b in range(session['blocks']) for r in range(session['repetitions'])}
        got=collections.Counter((r.get('condition'),r.get('method'),r.get('block_id'),r.get('rep_index')) for r in rows)
        if len(rows)!=session['total_planned_trials']:
            errors.append(('<schedule>',f'Expected {session["total_planned_trials"]} attempts; received {len(rows)}'))
        for key in sorted(expected-set(got)):
            errors.append(('<schedule>','Missing scheduled cell '+repr(key)))
        for key in sorted(set(got)-expected,key=str):
            errors.append(('<schedule>','Unexpected scheduled cell '+repr(key)))
        for key,n in got.items():
            if n!=1:errors.append(('<schedule>',f'Cell {key!r} appears {n} times'))
        ids=[r.get('trial_id') for r in rows]
        if len(ids)!=len(set(ids)) or any(not x for x in ids):
            errors.append(('<schedule>','Duplicate or missing trial identifiers'))
    for row in rows:
        tid=row.get('trial_id','<missing ID>');r=[]
        required=('trial_id','phase','method','condition','block_id','rep_index',
                  'status','failed','events','all_requests','decision_uid')
        for key in required:
            if key not in row:errors.append((tid,'Missing required trial field '+key))
        if not row.get('failed'):
            for key in ('guard_decision','guard','oracle_at_use','delete_sent',
                        'server_http_status','effect'):
                if key not in row:errors.append((tid,'Missing result field '+key))
        if row.get('failed'):warnings.append((tid,'Failed attempt retained: '+row.get('error','unknown error')))
        raw=row.get('all_requests',[])
        if not isinstance(raw,list):
            errors.append((tid,'all_requests must be a list'));continue
        if row.get('all_request_count') is not None and row['all_request_count']!=len(raw):
            errors.append((tid,'all_request_count disagrees with captured requests'))
        dispatch=[x for x in raw if x.get('kind')=='guard' and x.get('method')=='DELETE']
        if bool(dispatch)!=bool(row.get('delete_sent')):
            errors.append((tid,'delete_sent disagrees with actual captured request log'))
        if row.get('guard_decision') in ('ABORT_REPLAN','UNKNOWN_COHERENCE') and dispatch:
            errors.append((tid,'DELETE dispatched after aborted/unknown gate'))
        for req in dispatch:
            if req.get('delete_options',{}).get('preconditions',{}).get('uid')!=row.get('decision_uid'):
                errors.append((tid,'DELETE missing/mismatched UID precondition in actual request'))
        if row.get('server_http_status')==409 and row.get('effect',{}).get('effect_verified'):
            errors.append((tid,'HTTP 409 counted as verified deletion'))
        if row.get('condition')=='postgate_uid' and row.get('guard_decision')=='EXECUTE':
            if row.get('server_http_status')!=409:
                errors.append((tid,'Expected real UID mismatch 409 not observed: investigate'))
            if row.get('effect',{}).get('effect_verified'):
                errors.append((tid,'Replacement counted as deletion effect'))
        if row.get('condition') in ('postgate_evidence','postgate_scope','postgate_application_policy'):
            if row.get('guard_decision')=='EXECUTE' and row.get('oracle_at_use',{}).get('decision')!='ABORT_REPLAN':
                errors.append((tid,'Postgate injector did not produce expected invalid use-time state'))
        g=row.get('guard',{})
        if g.get('guard_get_count') is not None:
            measured=sum(x.get('kind')=='guard' and x.get('method')=='GET' for x in raw)
            if g['guard_get_count']!=measured:
                errors.append((tid,'Reported GET count disagrees with captured requests'))
        if row.get('method') in ('DCJI','FullRefresh') and g.get('decision') not in (None,'UNKNOWN_COHERENCE'):
            expected=8 if row['method']=='DCJI' else 11
            if g.get('guard_get_count')!=expected:
                errors.append((tid,f'Measured GET count != expected {expected}'))
            if g.get('ssar_posts')!=1:errors.append((tid,'Missing one SelfSubjectAccessReview POST'))
        if row.get('condition')=='symmetric_bdf_stress' and row.get('method')=='DCJI':
            warnings.append((tid,'OUT-OF-MODEL STRESS; do not pool with primary attack-capability guarantee'))
        if row.get('guard_decision')=='EXECUTE' and row.get('oracle_gate_decision') not in (None,'EXECUTE'):
            warnings.append((tid,'Guard/oracle gate disagreement: preserve raw as unfavorable outcome'))
        if row.get('oracle_at_use',{}).get('decision')=='ABORT_REPLAN' and row.get('server_http_status') in (200,202):
            warnings.append((tid,'USE-TIME INVALID ACTION SERVER ACCEPTED: negative safety boundary'))
    return {'total_attempts_checked':len(rows),'hard_accounting_errors':errors,
      'limitations_and_negative_results':warnings,'do_not_drop_rows':True}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('run_dir',type=Path);a=ap.parse_args()
    raw=a.run_dir/'RAW_TRIALS.jsonl'
    rows=[json.loads(x) for x in raw.read_text().splitlines() if x.strip()]
    session=json.loads((a.run_dir/'SESSION.json').read_text())
    outcome=audit(rows,session)
    status=a.run_dir/'COMPLETE_OR_INTERRUPTED.json'
    if status.exists():
        import hashlib
        meta=json.loads(status.read_text())
        if meta.get('written')!=len(rows):
            outcome['hard_accounting_errors'].append(('<schedule>','Interruption ledger written count mismatch'))
        if meta.get('completed') is False and len(rows)==session['total_planned_trials']:
            outcome['hard_accounting_errors'].append(('<schedule>','Run recorded interrupted despite full trial count; inspect interruption'))
        if meta.get('attempts_started') is not None and meta['attempts_started']<len(rows):
            outcome['hard_accounting_errors'].append(('<schedule>','Attempt-started count smaller than durable rows'))
        if meta.get('raw_sha256')!=hashlib.sha256(raw.read_bytes()).hexdigest():
            outcome['hard_accounting_errors'].append(('<schedule>','Interruption ledger raw SHA256 mismatch'))
    (a.run_dir/'RAW_INTEGRITY_REPORT.json').write_text(json.dumps(outcome,indent=2)+'\n')
    print(f"Audited {len(rows)} rows: {len(outcome['hard_accounting_errors'])} hard errors, "
          f"{len(outcome['limitations_and_negative_results'])} recorded cautions.")
    return 2 if outcome['hard_accounting_errors'] else 0
if __name__=='__main__':sys.exit(main())
