#!/usr/bin/env python3
import csv, json, math, os, sys
from collections import Counter, defaultdict

def wilson_upper_zero(n, z=1.959963984540054):
    return z*z/(n+z*z) if n else None

def analyze(run_dir, oracle_code_dir):
    sys.path.insert(0, oracle_code_dir)
    import oracle_e39
    raw=[]; audit_ids=set(); uids=set()
    with open(os.path.join(run_dir,'RAW_TRIALS.jsonl'), encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            t=json.loads(line); raw.append(t)
            for part in ('writer','delete'):
                aid=(t.get(part) or {}).get('audit_id')
                if aid: audit_ids.add(str(aid))
            uid=(t.get('fixture') or {}).get('pod_uid_decision')
            if uid: uids.add(str(uid))
    audit=[]
    with open(os.path.join(run_dir,'audit.log'), encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            try: e=json.loads(line)
            except Exception: continue
            aid=str(e.get('auditID') or '')
            ref=e.get('objectRef') or {}
            ro=e.get('responseObject') if isinstance(e.get('responseObject'),dict) else {}
            keep=aid in audit_ids
            if (not keep and e.get('stage')=='ResponseComplete' and
                ref.get('resource')=='pods' and e.get('verb') in ('update','patch') and
                ro.get('kind')=='Pod'):
                keep=True
            if keep: audit.append(e)
    with open(os.path.join(run_dir,'webhook_log.jsonl'), encoding='utf-8') as f:
        webhook=[json.loads(x) for x in f if x.strip()]
    pod_events=[]
    with open(os.path.join(run_dir,'pod_events.jsonl'), encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            e=json.loads(line)
            if str(e.get('uid') or '') in uids: pod_events.append(e)
    aidx=oracle_e39.index_audit(audit)
    widx=oracle_e39.index_webhook(webhook)
    pidx=oracle_e39.index_pod_events(pod_events)
    rows=[oracle_e39.classify_trial(t,aidx,widx,pidx) for t in raw]
    async_rows=[r for r in rows if str(r.get('condition_c','')).startswith('async_')]
    challenges=[r for r in async_rows if r.get('primary_class')=='CORRECT_ABORT' and
                r.get('decider')=='rv_precondition' and r.get('conflict_attribution')=='writer']
    stale=[r for r in async_rows if r.get('primary_class')=='INVALID_EXECUTED']
    cell=defaultdict(lambda: {'trials':0,'challenges':0,'stale_accepts':0,'valid_executed':0})
    for r in async_rows:
        d=float(r.get('delta_ms'))
        c=cell[d]; c['trials']+=1
        if r.get('primary_class')=='CORRECT_ABORT' and r.get('decider')=='rv_precondition' and r.get('conflict_attribution')=='writer': c['challenges']+=1
        elif r.get('primary_class')=='INVALID_EXECUTED': c['stale_accepts']+=1
        elif r.get('primary_class')=='VALID_EXECUTED': c['valid_executed']+=1
    summary={
      'analysis':'frozen E39 oracle with retained E52 RequestResponse audit evidence',
      'raw_trials':len(raw),
      'async_a4d_trials':len(async_rows),
      'primary_class_counts':dict(sorted(Counter(r['primary_class'] for r in rows).items())),
      'async_primary_class_counts':dict(sorted(Counter(r['primary_class'] for r in async_rows).items())),
      'writer_attributed_rv_precondition_challenges':len(challenges),
      'stale_accepts_among_challenges':len(stale),
      'wilson95_upper_if_zero':wilson_upper_zero(len(challenges)+len(stale)),
      'no_write_controls':sum(1 for r in rows if r.get('condition_c')=='no_write'),
      'uid_rebind_controls':sum(1 for r in rows if r.get('condition_c')=='uid_rebind_control'),
      'note':'E52 is a separate replication and is not pooled with the primary 0/42 study.'
    }
    cells=[]
    for d in sorted(cell):
        x=cell[d]
        cells.append({'delta_ms':d, **x})
    return summary,cells

def main():
    if len(sys.argv)!=4:
        raise SystemExit('usage: analyze_e52_with_frozen_e39_oracle.py RUN_DIR ORACLE_CODE_DIR OUT_DIR')
    run, oracle, out=sys.argv[1:]
    os.makedirs(out,exist_ok=True)
    summary,cells=analyze(run,oracle)
    with open(os.path.join(out,'E52_frozen_oracle_summary.json'),'w',encoding='utf-8',newline='\n') as f:
        json.dump(summary,f,indent=2,sort_keys=True); f.write('\n')
    with open(os.path.join(out,'E52_frozen_oracle_cells.csv'),'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['delta_ms','trials','challenges','stale_accepts','valid_executed'],lineterminator='\n')
        w.writeheader(); w.writerows(cells)
    print(json.dumps(summary,indent=2,sort_keys=True))
if __name__=='__main__': main()
