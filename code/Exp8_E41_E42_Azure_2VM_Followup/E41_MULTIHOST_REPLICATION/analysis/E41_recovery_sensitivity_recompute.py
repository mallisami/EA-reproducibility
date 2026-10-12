#!/usr/bin/env python3
import argparse, csv, json, pathlib, collections, hashlib

p=argparse.ArgumentParser()
p.add_argument('--results', required=True, help='E41 results directory')
p.add_argument('--outdir', required=True)
a=p.parse_args()
root=pathlib.Path(a.results)
out=pathlib.Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

# Inputs
oracle=list(csv.DictReader(open(root/'analysis_e41'/'oracle_trials_E39.csv', newline='')))
trials={str(x['trial_id']):x for x in map(json.loads, open(root/'RAW_TRIALS.jsonl'))}
events=[]
for line in open(root/'pod_events.jsonl'):
    line=line.strip()
    if line:
        events.append(json.loads(line))
by_uid_rv=collections.defaultdict(list)
for e in events:
    uid=e.get('uid'); rv=e.get('rv')
    if uid is not None and rv is not None:
        by_uid_rv[(str(uid),str(rv))].append(e)

invalid=[r for r in oracle if r.get('arm_c')=='A4d' and r.get('primary_class')=='INVALID_EXECUTED']
rows=[]
for r in invalid:
    tid=str(r['trial_id']); t=trials[tid]
    uid=str((t.get('delete') or {}).get('expected_uid') or (t.get('fixture') or {}).get('pod_uid_decision') or '')
    wrv=str((t.get('writer') or {}).get('resulting_resource_version') or '')
    matches=by_uid_rv.get((uid,wrv),[])
    term=[e for e in matches if e.get('type')=='MODIFIED' and e.get('deletion_timestamp')]
    accepted=(t.get('delete') or {}).get('http_status') in (200,201,202)
    exact_sig=bool(accepted and len(term)>=1)
    rows.append({
        'trial_id':tid,
        'condition':r.get('condition_c'),
        'delta_ms':r.get('delta_ms'),
        'primary_class_frozen':r.get('primary_class'),
        'primary_reason_frozen':r.get('primary_reason'),
        'flags_frozen':r.get('flags'),
        'delete_http_status':(t.get('delete') or {}).get('http_status'),
        'expected_uid':uid,
        'precondition_rv':r.get('precondition_rv'),
        'writer_rv':wrv,
        'action_rv':r.get('action_rv'),
        'pod_event_matches':len(matches),
        'terminating_modified_matches':len(term),
        'pod_event_type':'|'.join(sorted({str(e.get('type')) for e in matches})),
        'deletion_timestamp':'|'.join(sorted({str(e.get('deletion_timestamp')) for e in term})),
        'prelocked_2_3f_signature':int(exact_sig),
        'sensitivity_class':'VALID_EXECUTED' if exact_sig else r.get('primary_class'),
        'sensitivity_reason':'writer_on_terminating_pod_event_recovery' if exact_sig else r.get('primary_reason'),
    })

csv_path=out/'E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.csv'
with open(csv_path,'w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys()) if rows else ['trial_id']); w.writeheader(); w.writerows(rows)

all_sig=sum(int(x['prelocked_2_3f_signature']) for x in rows)
# Frozen counts all arms and sensitivity counts; only reclassify exact-signature A4d INVALIDs
frozen=collections.Counter((r['arm_c'],r['primary_class']) for r in oracle)
sens=collections.Counter()
for r in oracle:
    cls=r['primary_class']
    if r.get('arm_c')=='A4d' and cls=='INVALID_EXECUTED':
        rr=next((x for x in rows if x['trial_id']==r['trial_id']),None)
        if rr and rr['prelocked_2_3f_signature']:
            cls='VALID_EXECUTED'
    sens[(r['arm_c'],cls)]+=1

# async-only
async_conditions={'async_scope','async_app_policy','async_evidence_last_literal'}
frozen_async=collections.Counter((r['arm_c'],r['primary_class']) for r in oracle if r.get('condition_c') in async_conditions)
sens_async=collections.Counter()
for r in oracle:
    if r.get('condition_c') not in async_conditions: continue
    cls=r['primary_class']
    if r.get('arm_c')=='A4d' and cls=='INVALID_EXECUTED':
        rr=next((x for x in rows if x['trial_id']==r['trial_id']),None)
        if rr and rr['prelocked_2_3f_signature']:
            cls='VALID_EXECUTED'
    sens_async[(r['arm_c'],cls)]+=1

by_cond_delta=collections.Counter((x['condition'],x['delta_ms']) for x in rows if x['prelocked_2_3f_signature'])
summary={
    'schema':'e41.audit_retention_recovery_sensitivity.v1',
    'basis':'Post-hoc recovery sensitivity using the complete Pod-event recorder to detect the pre-LOCK ANALYSIS_PLAN_E39 rule 2.3(f) signature when writer audit responseObject records were lost to audit-log rotation.',
    'frozen_primary_a4d_invalid':len(rows),
    'a4d_invalid_matching_prelocked_2_3f_signature':all_sig,
    'all_frozen_a4d_invalid_match_signature': all_sig==len(rows),
    'frozen_counts':{arm:{cls:n for (a,cls),n in frozen.items() if a==arm} for arm in sorted({a for a,_ in frozen})},
    'sensitivity_counts':{arm:{cls:n for (a,cls),n in sens.items() if a==arm} for arm in sorted({a for a,_ in sens})},
    'frozen_async_counts':{arm:{cls:n for (a,cls),n in frozen_async.items() if a==arm} for arm in sorted({a for a,_ in frozen_async})},
    'sensitivity_async_counts':{arm:{cls:n for (a,cls),n in sens_async.items() if a==arm} for arm in sorted({a for a,_ in sens_async})},
    'recovered_signature_by_condition_delay':[{'condition':c,'delta_ms':float(d),'n':n} for (c,d),n in sorted(by_cond_delta.items(),key=lambda z:(z[0][0],float(z[0][1])))],
    'qualification':'This does not recreate the missing audit responseObject. It is a sensitivity analysis grounded in an independently recorded Pod event stream and the pre-specified terminating-Pod rule. The planned ResponseComplete sensitivity cannot be reconstructed for audit records that rotated out.'
}
json_path=out/'E41_AUDIT_RETENTION_RECOVERY_SENSITIVITY.json'
json.dump(summary,open(json_path,'w'),indent=2,sort_keys=True); open(json_path,'a').write('\n')

print(json.dumps(summary,indent=2))
for path in (csv_path,json_path):
    h=hashlib.sha256(path.read_bytes()).hexdigest(); print(path.name,h)
