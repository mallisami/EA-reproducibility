#!/usr/bin/env python3
"""A4d-specific live preflight; diagnostic only and never pooled with confirmation data."""
from __future__ import annotations
import argparse,json,sys,time,traceback
from pathlib import Path
HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'A4D_CONFIRMATION'/'scripts')); import a4d_confirmation  # noqa:E402,F401
BASE=ROOT/'shared'/'E39_LOCKED_BASE'; sys.path.insert(0,str(BASE/'src'))
import arms_commit as AC  # noqa:E402
import e39_kube as K  # noqa:E402
import e38v2_runner as R38  # noqa:E402
from e39_kube import E39Client  # noqa:E402
API='https://127.0.0.1:6443'
def rv(obj): return ((obj or {}).get('metadata') or {}).get('resourceVersion')
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--run-id',required=True); ap.add_argument('--token-dir',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    ns='e39-'+a.run_id; tok=Path(a.token_dir); ops=[]; clients={}
    def cl(ident,pod=K.POD_NAME):
        key=(ident,pod)
        if key not in clients: clients[key]=E39Client(API,ns,ident,token_path=str(tok/f'{ident}.token'),ca_path=str(tok/'ca.crt'),pod_name=pod)
        return clients[key]
    def rec(op,ident,st,h,obj,**kw):
        d={'op':op,'identity':ident,'status':st,'audit_id':h.get('Audit-Id') if h else None,'rv':rv(obj),'t_wall_ns':time.time_ns(),**kw}; ops.append(d); return d
    def delete_if_present(name):
        c=cl('reset',name); p,_=c.get_pod()
        if p is not None: st,h,o=c.delete_pod(p['metadata']['uid']); rec('cleanup_delete','reset',st,h,o,pod=name)
    result={'schema':'a4d.preflight.v1','run_id':a.run_id,'namespace':ns,'parts':{},'ops':ops,'t_start_wall_ns':time.time_ns()}
    try:
        ids={i:cl(i).whoami() for i in ('responder','writer','reset','mirror')}; want={i:'system:serviceaccount:e39-system:e39-'+i for i in ids}
        result['parts']['identity']={'got':ids,'want':want,'PASS':ids==want}
        if ids!=want: raise RuntimeError('identity mismatch')
        name='probe-a4d-audit-safe'; delete_if_present(name); c=cl('reset',name)
        st,h,o=c.create_target_pod(annotations=AC.fixture_annotations()); rec('create_pod','reset',st,h,o,pod=name)
        if st not in (200,201): raise RuntimeError(f'create HTTP {st}')
        uid=o['metadata']['uid']; R38.wait_pod_ready(c,uid,timeout_s=120); p,_=cl('responder',name).get_pod(); node=((p or {}).get('spec') or {}).get('nodeName')
        if node!='a4d-worker': raise RuntimeError(f'target scheduled on {node}, expected a4d-worker')
        rv0=p['metadata']['resourceVersion']
        st,h,o=cl('writer',name).patch_pod_annotations({K.SCOPE_ANN:'false'}); wr=rec('writer_patch','writer',st,h,o,pod=name)
        st,h,o=cl('responder',name).delete_pod(uid,rv0); stale=rec('delete_stale_rv','responder',st,h,o,pod=name,precondition_rv=rv0,message=(o or {}).get('message') if isinstance(o,dict) else None)
        st,h,o=cl('writer',name).patch_pod_annotations({K.SCOPE_ANN:'true'}); rec('writer_restore','writer',st,h,o,pod=name)
        p,_=cl('responder',name).get_pod(); rvc=p['metadata']['resourceVersion']
        st,h,o=cl('responder',name).delete_pod(uid,rvc); cur=rec('delete_current_rv','responder',st,h,o,pod=name,precondition_rv=rvc,expected_uid=uid)
        result['parts']['a4d']={'worker_node':node,'writer_rv':wr['rv'],'stale_status':stale['status'],'stale_message':stale.get('message'),'current_status':cur['status'],'PASS':node=='a4d-worker' and wr['status']==200 and stale['status']==409 and 'ResourceVersion in the precondition' in (stale.get('message') or '') and cur['status'] in (200,202)}
        result['ALL_API_PASS']=all(v.get('PASS') for v in result['parts'].values())
    except Exception as e:
        result['exception']=repr(e); result['traceback']=traceback.format_exc(); result['ALL_API_PASS']=False
    finally:
        try: delete_if_present('probe-a4d-audit-safe')
        except Exception: pass
        for c in clients.values():
            try:c.close()
            except Exception:pass
    result['t_end_wall_ns']=time.time_ns(); Path(a.out).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'ALL_API_PASS':result.get('ALL_API_PASS'),'parts':{k:v.get('PASS') for k,v in result['parts'].items()},'exception':result.get('exception')},indent=2)); return 0 if result.get('ALL_API_PASS') else 3
if __name__=='__main__': raise SystemExit(main())
