#!/usr/bin/env python3
"""Verify integrated E43/E46/E47/E48/E49/E50 scope-strengthening evidence.

Checks trial counts, frozen summaries, campaign-level prospective execution discipline,
and the denominator/ordering boundaries used in the synchronized manuscript.
"""
from pathlib import Path
import csv, json, sys
ROOT=Path(__file__).resolve().parents[1]
EX=ROOT/'experiments'

def load(rel): return json.load(open(EX/rel/'summary.json',encoding='utf-8'))
def cells(rel): return list(csv.DictReader(open(EX/rel/'cells.csv',encoding='utf-8')))
def count_trials(rel): return sum(1 for x in open(EX/rel/'trials.jsonl',encoding='utf-8') if x.strip())
def isum(rr, field): return sum(int(r[field]) for r in rr)
def check(cond,msg):
    global ncheck
    ncheck+=1
    if cond: print('PASS:',msg)
    else: failures.append(msg); print('FAIL:',msg)

failures=[]; ncheck=0
runs=[
 ('Exp11_E43_Grace_Period_Sensitivity/E43_serial_check',30),
 ('Exp11_E43_Grace_Period_Sensitivity/E43',2504),
 ('Exp12_E46_Target_State_Admission_vs_RV_Binding/E46',2943),
 ('Exp13_E47_Retry_Policy/E47',1140),
 ('Exp14_E48_Quarantine_PATCH/E48',1796),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep1',624),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep2',624),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep3',624),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep4',624),
 ('Exp15_E49_Fresh_Cluster_Replication/E49_rep5',624),
 ('Exp16_E50_Replicated_Control_Plane_Topology/E50_ha',624),
]
total=0
for rel,expected in runs:
    n=count_trials(rel); total+=n; check(n==expected,f'{rel}: {n}/{expected} recorded trials')
check(total==12157,f'campaign total = 12,157 recorded trials (got {total})')

manifest=(EX/'Scope_Strengthening_E43_E50_20261003'/'EXECUTION_MANIFEST.md').read_text(encoding='utf-8')
check('Total planned primary experiment trials across E43/E46/E47/E48/E49/E50: **12,127**' in manifest,'campaign manifest fixes 12,127 primary trials')
check('Run all six experiments. Preserve all outcomes.' in manifest,'campaign manifest requires all six studies and all outcomes')
check('Do not stop because a hypothesis is contradicted unless a pre-specified technical stop rule is triggered.' in manifest,'campaign manifest excludes hypothesis-based stopping')

# E43: protected boundary vs later effect
s=load('Exp11_E43_Grace_Period_Sensitivity/E43')
check(s['trials']==2504,'E43 summary trial count = 2,504')
check(s['A4d_contested_and_rejected']==21,'E43 A4d protected-boundary contests/rejections = 21')
check(s['A4d_stale_accept']==0,'E43 observed stale accepts at protected DELETE boundary = 0')
check(s['readback_postmark_patches']==764 and s['readback_postmark_persisted']==659,'E43 later-effect read-back = 659/764 post-mark PATCH attempts persisted')

# E46: distinguish mechanical summary field from interpretable race denominator.
rel='Exp12_E46_Target_State_Admission_vs_RV_Binding/E46'; s=load(rel); rr=cells(rel)
a4=s['by_arm_policy']['A4d/none']; a5=s['by_arm_policy']['A5v/none']
check(a4['n']==1077 and a4['stale_accept']==0 and a4['benign_abort']==188,'E46 A4d: n=1,077; 0 stale accepts; 188 benign aborts')
check(a5['n']==1866 and a5['stale_accept']==0 and a5['benign_abort']==0,'E46 A5v: n=1,866; 0 stale accepts; 0 benign aborts')
known=[r for r in rr if r['block']=='inv-hold20' and r['arm']=='A5v' and r['cond']=='scope' and r['delta_ms'] in {'0','2','5','10'}]
extra_nohold=[r for r in rr if r['block']=='inv' and r['arm']=='A5v' and r['cond']=='scope']
extra40=[r for r in rr if r['block']=='inv-hold20' and r['arm']=='A5v' and r['cond']=='scope' and r['delta_ms']=='40']
known_denies=isum(known,'POLICY_DENY'); extra_denies=isum(extra_nohold,'POLICY_DENY')+isum(extra40,'POLICY_DENY')
check(known_denies==600,'E46 predicate-specific admission: 600 known pre-dispatch invalidation denials')
check(extra_denies==9,'E46 predicate-specific admission: 9 additional order-unresolved policy denials')
check(a5['contested']==609 and known_denies+extra_denies==609,'E46 frozen summary field totals 609, decomposed as 600 known pre-dispatch + 9 order-unresolved denials')

# E47: use matched invalidating-write safety denominator, not the larger per-policy total.
rel='Exp13_E47_Retry_Policy/E47'; s=load(rel); rr=cells(rel)
blind_safety=[r for r in rr if r['block']=='safety' and r['arm']=='A4d' and r['policy']=='blind' and r['cond']=='scope']
rev_safety=[r for r in rr if r['block']=='safety' and r['arm']=='A4d' and r['policy']=='revalidate' and r['cond']=='scope']
check(isum(blind_safety,'n')==180 and isum(blind_safety,'stale_accept')==176,'E47 blind retry safety: 176/180 stale accepts in invalidating-write trials')
check([int(r['stale_accept']) for r in blind_safety]==[59,59,58],'E47 blind retry safety cells = 59/60, 59/60, 58/60')
check(isum(rev_safety,'n')==180 and isum(rev_safety,'stale_accept')==0,'E47 revalidating retry safety: 0/180 stale accepts in invalidating-write trials')
check(s['by_arm_policy']['A4d/blind']['n']==380 and s['by_arm_policy']['A4d/revalidate']['n']==380,'E47 larger 380-trial per-policy summaries include safety controls and liveness conditions')

# E48
s=load('Exp14_E48_Quarantine_PATCH/E48'); u=s['by_arm_policy']['A6u/none']; b=s['by_arm_policy']['A6b/none']
check(u['n']==898 and u['stale_accept']==348 and u['EXECUTED']==898,'E48 unbound PATCH: 348 stale accepts / 898')
check(b['n']==898 and b['contested']==351 and b['stale_accept']==0 and b['EXECUTED']==547,'E48 bound PATCH: 0 stale accepts among 351 contests; 547 executed')

# E49
cont=[]
for i in range(1,6):
    s=load(f'Exp15_E49_Fresh_Cluster_Replication/E49_rep{i}'); a=s['by_arm_policy']['A4d/none']; cont.append(a['contested'])
    check(s['trials']==624 and a['stale_accept']==0,f'E49 rep{i}: 624 trials and 0 observed A4d stale accepts')
check(cont==[5,5,7,4,6] and sum(cont)==27,'E49 A4d contest counts = [5,5,7,4,6], total 27')

# E50
s=load('Exp16_E50_Replicated_Control_Plane_Topology/E50_ha'); a=s['by_arm_policy']['A4d/none']
check(s['trials']==624 and a['contested']==7 and a['stale_accept']==0,'E50: 624 trials, 7 A4d contests, 0 observed stale accepts')

for rel,_ in runs:
    s=load(rel)
    if 'by_arm_policy' in s:
        infra=sum(v.get('INFRA_ERROR',0) for v in s['by_arm_policy'].values())
        unk=sum(v.get('UNKNOWN',0) for v in s['by_arm_policy'].values())
        check(infra==0 and unk==0,f'{rel}: 0 INFRA_ERROR and 0 UNKNOWN in frozen summary')

print('\nChecks:',ncheck,'Failures:',len(failures))
if failures: sys.exit(1)
print('SCOPE-STRENGTHENING VERIFICATION: PASS')
