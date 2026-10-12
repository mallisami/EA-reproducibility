#!/usr/bin/env python3
"""Compare updated-paper numeric plot inputs with canonical results and raw Pod events.

Tables S1/S8: counts, probabilities, Wilson limits and recorded sensitivities.
S8a: final-removal vs deletion-start marker counts, using the recorded Pod-event revisions.
S10a: all 400-response writer-stress cells. S33: fixed-stage chronological subsets.
No manuscript file is required. Outputs go to outputs/figure_inputs/.
"""
import argparse, ast, csv, json, re, sys, tarfile
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
EX=ROOT/'experiments'
INPUTS=ROOT/'data/figure_inputs'
OUT=ROOT/'outputs/figure_inputs'
def rows(p):
    with p.open(newline='',encoding='utf-8') as f:return list(csv.DictReader(f))
def write(p,rs):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rs[0]),lineterminator='\n');w.writeheader();w.writerows(rs)
def number(s): return float(re.search(r'[0-9.]+',s).group())
def same(x,y,places=3):return abs(float(x)-float(y)) <= 0.5*10**(-places)+1e-8
def ci(s):return [float(x) for x in re.findall(r'[0-9.]+',s)]
COND={'scope':'async_scope','app-policy':'async_app_policy','evidence':'async_evidence_last_literal',
      'no write':'no_write','no-write':'no_write','UID rebind':'uid_rebind_control',
      'uid-rebind':'uid_rebind_control','uid rebind':'uid_rebind_control'}
ARMS={'A0':'A0_native_uid','A1':'A1_dcji','A2':'A2_fullrefresh','A3':'A3_dcji_webhook',
      'A4m':'A4m_commit_mirror','A4d':'A4d_commit_direct'}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--quick',action='store_true',help='skip raw deletion-marker replay')
    args=ap.parse_args(); failures=[];checks=[]
    def check(name,ok):
        checks.append((name,bool(ok)))
        if not ok: failures.append(name);print('FAIL:',name)
    x5=EX/'Exp5_E38v2_Actuation_Exposure/Confirmatory/data/processed'
    x6=EX/'Exp6_E39_Commit_Bound_Enforcement/data/processed'
    e38=rows(x5/'analysis_final/cells_E38v2.csv')
    e39=rows(x6/'analysis_final/cells_E39.csv')
    def delta(s):return 'NA' if s in ['NA','--',''] else float(s)
    m38={(r['arm'],r['condition'],delta(r['delta_ms'])):r for r in e38}
    m39={tuple(r['cell'].split('/')[1:]):r for r in e39 if r['experiment']=='A'}
    for label,m in [('tab-S1',m38),('tab-S8',m39)]:
        for i,r in enumerate(rows(INPUTS/(label+'.csv'))):
            cond=COND.get(r['write'])
            if cond is None:raise ValueError('Unknown condition: '+r['write'])
            if label=='tab-S1':q=m.get((ARMS[r['arm']],cond,delta(r['delta'])))
            else:
                ds='NA' if r['delta']=='NA' else f"{float(r['delta']):g}"
                q=m.get((r['arm'],'python',cond,ds))
            check(f'{label} row {i+1} canonical cell',q is not None)
            if q is None:continue
            fields={'n':'n_analyzable','k':'k_invalid_executed','p':'p_hat',
                    'sec_p':'sec_p_hat','ter_p':'ter_p_hat','version_conflict':'n_rv_precondition_409',
                    'ca':'n_CORRECT_ABORT','unk':'n_UNKNOWN'}
            for a,b in fields.items():
                if a not in r or r[a] in ['','--','NA']:continue
                check(f'{label} row {i+1} {a}',same(r[a],q[b],3 if a.endswith('_p') or a=='p' else 0))
            bounds=ci(r['ci'])
            check(f'{label} row {i+1} Wilson',len(bounds)==2 and all(same(a,q[b]) for a,b in zip(bounds,['wilson_lo','wilson_hi'])))
            sk='stage1_k';sn='stage1_n_analyzable' if label=='tab-S1' else 'stage1_n'
            if r['stage1'] not in ['','--']:
                check(f'{label} row {i+1} stage 1',r['stage1']==f"{q[sk]}/{q[sn]}")
    mb={tuple(r['cell'].split('/')[1:]):r for r in e39 if r['experiment']=='B'}
    for r in rows(INPUTS/'tab-S10a.csv'):
        q=mb[(r['arm'],'async_scope',f"{float(r['lambda']):g}")]
        check(f"S10a {r['arm']} {r['lambda']} responses",q['n_analyzable']=='400')
        for a,b in [('invalid_ci','n_INVALID_EXECUTED'),('abort','n_CORRECT_ABORT'),('valid','n_VALID_EXECUTED'),('conflict','n_rv_precondition_409')]:
            check(f"S10a {r['arm']} {r['lambda']} {a}",same(number(r[a]),1000*float(q[b])/400,1))
        check(f"S10a {r['arm']} {r['lambda']} interval",all(same(a,1000*float(q[b]),1) for a,b in zip(ci(r['invalid_ci'])[1:],['wilson_lo','wilson_hi'])))

    oracle=rows(x6/'analysis_final/oracle_trials_E39.csv')
    asyncs=[r for r in oracle if r['experiment']=='A' and r['condition'] in set(COND.values())-{'no_write','uid_rebind_control'}]
    groups=defaultdict(list)
    for r in asyncs:
        if r['primary_class'] not in ['UNKNOWN','INFRA_ERROR']:groups[(r['arm_c'],float(r['delta_ms']))].append(r)
    endpoints=rows(INPUTS/'tab-S8a.csv')
    for r in endpoints:
        g=groups[(r['arm'],float(r['delta']))]
        check(f"S8a {r['arm']} {r['delta']} n",len(g)==int(r['n']))
        check(f"S8a {r['arm']} {r['delta']} final",sum(x['primary_class']=='INVALID_EXECUTED' for x in g)==int(r['final']))
    if not args.quick:
        # First recorded revision carrying deletionTimestamp marks deletion start.
        # The primary final-removal classification remains unchanged.
        marks={}
        archive=EX/'Exp6_E39_Commit_Bound_Enforcement/data/raw/e39_EXPORT_e39-20260926t1515z.REDACTED.tar.gz'
        with tarfile.open(archive,'r|gz') as tf:
            for member in tf:
                if member.name!='e39-20260926t1515z/pod_events.jsonl':continue
                for line in tf.extractfile(member):
                    e=json.loads(line)
                    if e.get('deletion_timestamp') and e.get('rv') and e.get('uid'):
                        rv=int(e['rv']);uid=e['uid'];marks[uid]=min(marks.get(uid,rv),rv)
                break
        marker_rows=[]
        for r in endpoints:
            g=groups[(r['arm'],float(r['delta']))];k=0
            for x in g:
                if x['primary_class']!='INVALID_EXECUTED':continue
                uid=x['expected_uid']
                if uid not in marks:raise ValueError('Missing deletion marker: '+uid)
                if x['first_write_rv'] and int(x['first_write_rv'])<marks[uid]:k+=1
            check(f"S8a {r['arm']} {r['delta']} deletion marker",k==int(r['mark']))
            marker_rows.append(dict(arm=r['arm'],delta=r['delta'],n=len(g),final=r['final'],mark=k))
        write(OUT/'endpoint_counts.csv',marker_rows)

    fixed38=[r for r in rows(x5/'analysis_final/oracle_trials_E38v2.csv') if r['stage']=='1' and int(r['block'])<=38]
    fixed39=[r for r in rows(x6/'analysis_A_stage1/oracle_trials_E39.csv') if r['stage']=='1' and int(r['block'])<=38]
    periods=[range(0,10),range(10,20),range(20,30),range(30,39)]
    conds={'async_scope','async_app_policy','async_evidence_last_literal'}
    specs=[('client',fixed38,lambda r:r['arm'] in {'A0_native_uid','A1_dcji','A2_fullrefresh'} and r['condition'] in conds and float(r['delta_ms']) in {0,.5,1,2}),
      ('admission_scope_policy',fixed38,lambda r:r['arm']=='A3_dcji_webhook' and r['condition'] in {'async_scope','async_app_policy'} and float(r['delta_ms']) in {0,.5,1,2,3,5}),
      ('admission_evidence',fixed38,lambda r:r['arm']=='A3_dcji_webhook' and r['condition']=='async_evidence_last_literal' and float(r['delta_ms']) in {0,.5,1,2}),
      ('extended_client',fixed39,lambda r:r['arm']=='A1_dcji' and r['condition'] in conds),
      ('extended_admission',fixed39,lambda r:r['arm']=='A3_dcji_webhook' and r['condition'] in conds),
      ('mirror',fixed39,lambda r:r['arm']=='A4m_commit_mirror' and r['condition'] in conds),
      ('direct',fixed39,lambda r:r['arm']=='A4d_commit_direct' and r['condition'] in conds)]
    s33=[];expected=rows(INPUTS/'tab-S33.csv')
    for (label,rr,pred),exp in zip(specs,expected):
        vals=[]
        for br in periods:
            g=[r for r in rr if int(r['block']) in br and pred(r) and r['primary_class']!='UNKNOWN']
            vals.append((sum(r['primary_class']=='INVALID_EXECUTED' for r in g),len(g)))
        vals.append((sum(k for k,n in vals),sum(n for k,n in vals)))
        out=dict(subset=label)
        for key,(k,n) in zip(['b1_10','b11_20','b21_30','b31_39','overall'],vals):
            out[key]=f'{k}/{n}';check(f'S33 {label} {key}',out[key]==exp[key])
        s33.append(out)
    write(OUT/'chronological_blocks.csv',s33)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'verification.json').write_text(json.dumps({'passed':len(checks)-len(failures),'checks':len(checks),'failures':failures,'raw_marker_replayed':not args.quick},indent=2)+'\n')
    print(f'Figure/table numeric inputs: {len(checks)-len(failures)}/{len(checks)} checks passed')
    return 1 if failures else 0
if __name__=='__main__':sys.exit(main())
