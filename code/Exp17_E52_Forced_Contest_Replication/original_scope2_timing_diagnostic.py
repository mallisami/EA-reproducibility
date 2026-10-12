#!/usr/bin/env python3
"""E52 contest analysis — BOUNDARY-AWARE (temporal + resourceVersion), not final-removal.

A genuine pre-boundary contest requires BOTH:
  (a) the writer COMMITTED a covered change that advanced the Pod resourceVersion past the
      DELETE's precondition RV:  writer.resulting_resource_version > bound_state.pod_rv_at_gate; AND
  (b) that commit happened BEFORE the DELETE reached the protected boundary, established by the
      monotonic clock on the same host:  writer.t_resp_mono_ns < delete.t_send_mono_ns
      (the covered change was acknowledged before the DELETE was even dispatched).

A writer that committed AFTER the DELETE was dispatched/accepted (writer.t_resp_mono_ns >=
delete.t_send_mono_ns) is a LATE WRITER and is NOT a contest, even if its RV is higher -- the
covered change did not reach the boundary before the DELETE, so an accepted DELETE is correct.

Outcome of a contest, from the DELETE itself:
  writer_409   : delete.http_status==409 and conflict_kind=='resource_version' (correctly rejected)
  stale_accept : delete.http_status==200 (ACCEPTED despite a covered change that reached the boundary)
Ambiguous commits (writer acknowledged between DELETE send and response) are conservatively
NOT counted as contests; exact store-commit ordering requires the RequestResponse audit log
(captured by E51 on K3s). Final-removal INVALID_EXECUTED is never used.
"""
import argparse,json,math,os
def wil(k,n,z=1.959963984540054):
    if n==0: return (0.0,0.0)
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/d
    return (max(0,c-h),min(1,c+h))
def _i(x):
    try: return int(x)
    except (TypeError,ValueError): return None
def classify(r):
    w=r.get('writer') or {}; d=r.get('delete') or {}; bs=r.get('bound_state') or {}
    gate_rv=_i(bs.get('pod_rv_at_gate')) or _i((r.get('delete_preconditions') or {}).get('resource_version'))
    writer_rv=_i(w.get('resulting_resource_version'))
    w_tresp=w.get('t_resp_mono_ns'); d_tsend=d.get('t_send_mono_ns')
    committed = writer_rv is not None
    rv_advanced = committed and gate_rv is not None and writer_rv > gate_rv
    # temporal pre-boundary: writer commit acknowledged strictly before the DELETE was dispatched
    pre_boundary = (w_tresp is not None and d_tsend is not None and w_tresp < d_tsend)
    if not (rv_advanced and pre_boundary):
        reason = ('not_committed' if not committed else
                  'rv_not_advanced' if not rv_advanced else
                  'late_or_ambiguous_writer')   # writer landed at/after DELETE dispatch -> non-contest
        return (None, reason, gate_rv, writer_rv)
    http=d.get('http_status'); ck=d.get('conflict_kind')
    if http==409 and ck=='resource_version': return ('writer_409','pre_boundary',gate_rv,writer_rv)
    if http==200: return ('stale_accept','pre_boundary',gate_rv,writer_rv)
    return ('other_'+str(http),'pre_boundary',gate_rv,writer_rv)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--raw',required=True); ap.add_argument('--out-dir',required=True)
    a=ap.parse_args(); os.makedirs(a.out_dir,exist_ok=True)
    rows=[json.loads(l) for l in open(a.raw)]
    aa=[r for r in rows if r.get('arm','').startswith('A4d') and r.get('condition','').startswith('async')]
    diag=[]; c409=stale=other=0; excluded={'not_committed':0,'rv_not_advanced':0,'late_or_ambiguous_writer':0}
    for r in aa:
        kind,reason,grv,wrv=classify(r)
        if kind is None: excluded[reason]=excluded.get(reason,0)+1; continue
        if kind=='writer_409': c409+=1
        elif kind=='stale_accept': stale+=1
        else: other+=1
        diag.append({'trial_id':r.get('trial_id'),'delta_ms':r.get('delta_ms'),'gate_rv':grv,'writer_rv':wrv,
                     'delete_http':(r.get('delete') or {}).get('http_status'),'outcome':kind})
    contests=c409+stale; lo,hi=wil(stale,contests if contests else 1)
    summary={'async_a4d_trials':len(aa),'contests':contests,'writer_409':c409,'stale_accepts':stale,
             'pre_boundary_other':other,'excluded_non_contest':excluded,
             'stale_fraction':(stale/contests if contests else None),
             'wilson95_stale_upper':(hi if contests else None),
             'definition':'contest = RV advanced past precondition AND writer committed (t_resp) before DELETE dispatch (t_send); late/ambiguous writers excluded'}
    json.dump(summary,open(a.out_dir+'/E52_contest_summary.json','w'),indent=2,sort_keys=True)
    with open(a.out_dir+'/fence_diagnostics.jsonl','w') as f:
        for x in sorted(diag,key=lambda z:z['trial_id'] or 0): f.write(json.dumps(x,sort_keys=True)+'\n')
    print(json.dumps(summary,indent=2,sort_keys=True))
if __name__=='__main__': main()
