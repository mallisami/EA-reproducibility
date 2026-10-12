#!/usr/bin/env python3
"""Live E41 preflight probes. Diagnostic only; never pooled with E41 data."""
from __future__ import annotations
import argparse, json, sys, threading, time
from pathlib import Path

HERE=Path(__file__).resolve(); ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'E41_MULTIHOST_REPLICATION'/'scripts'))
import e41_main  # noqa: F401,E402; applies multi-host target Pod patch
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
        if key not in clients:
            clients[key]=E39Client(API,ns,ident,token_path=str(tok/f'{ident}.token'),ca_path=str(tok/'ca.crt'),pod_name=pod)
        return clients[key]
    def rec(op, ident, st, h, obj, **kw):
        d={'op':op,'identity':ident,'status':st,'audit_id':h.get('Audit-Id') if h else None,'rv':rv(obj),'t_wall_ns':time.time_ns(),**kw}; ops.append(d); return d
    def restore():
        for n in K.POLICY_CMS:
            st,h,o=cl('reset').patch_configmap(n,{K.CM_VALUE_KEY[n]:'true'}); rec('restore_cm','reset',st,h,o,object=n)
    def delete_if_present(name):
        c=cl('reset',name); p,_=c.get_pod()
        if p is not None:
            st,h,o=c.delete_pod(p['metadata']['uid']); rec('cleanup_delete','reset',st,h,o,pod=name)
    def make(name, webhook=False, anns=None):
        delete_if_present(name); c=cl('reset',name)
        st,h,o=c.create_target_pod(annotations=anns,webhook=webhook); rec('create_pod','reset',st,h,o,pod=name)
        if st not in (200,201): raise RuntimeError(f'create {name} HTTP {st}')
        uid=o['metadata']['uid']; R38.wait_pod_ready(c,uid,timeout_s=120)
        p,_=c.get_pod(); node=((p or {}).get('spec') or {}).get('nodeName')
        if node!='e41-worker': raise RuntimeError(f'{name} scheduled on {node}, expected e41-worker')
        return uid
    t0=time.time_ns(); result={'schema':'e41.preflight.v1','run_id':a.run_id,'namespace':ns,'t_start_wall_ns':t0,'parts':{},'ops':ops}
    try:
        # Identity gate.
        ids={i:cl(i).whoami() for i in ('responder','writer','reset','mirror')}
        want={i:'system:serviceaccount:e39-system:e39-'+i for i in ids}
        result['parts']['identity']={'got':ids,'want':want,'PASS':ids==want}
        if ids!=want: raise RuntimeError('identity mismatch')
        restore()
        # Admission deny/allow on a worker-host target.
        name='probe-e41-webhook'; uid=make(name,webhook=True)
        st,h,o=cl('writer').patch_configmap('application-policy',{'allowed':'false'}); rec('webhook_invalidate','writer',st,h,o)
        st,h,o=cl('responder',name).delete_pod(uid); denied=rec('webhook_delete_invalid','responder',st,h,o,pod=name)
        st,h,o=cl('writer').patch_configmap('application-policy',{'allowed':'true'}); rec('webhook_restore','writer',st,h,o)
        st,h,o=cl('responder',name).delete_pod(uid); allowed=rec('webhook_delete_valid','responder',st,h,o,pod=name)
        result['parts']['webhook']={'deny_status':denied['status'],'allow_status':allowed['status'],'PASS':denied['status']==403 and allowed['status'] in (200,202)}
        # A4d stale/current resourceVersion mechanics.
        name='probe-e41-a4d'; uid=make(name,anns=AC.fixture_annotations())
        p,_=cl('responder',name).get_pod(); rv0=p['metadata']['resourceVersion']
        st,h,o=cl('writer',name).patch_pod_annotations({K.SCOPE_ANN:'false'}); wr=rec('a4d_writer_patch','writer',st,h,o,pod=name)
        st,h,o=cl('responder',name).delete_pod(uid,rv0); stale=rec('a4d_delete_stale_rv','responder',st,h,o,pod=name,precondition_rv=rv0,message=(o or {}).get('message') if isinstance(o,dict) else None)
        st,h,o=cl('writer',name).patch_pod_annotations({K.SCOPE_ANN:'true'}); rec('a4d_restore','writer',st,h,o,pod=name)
        p,_=cl('responder',name).get_pod(); rvc=p['metadata']['resourceVersion']
        st,h,o=cl('responder',name).delete_pod(uid,rvc); cur=rec('a4d_delete_current_rv','responder',st,h,o,pod=name,precondition_rv=rvc)
        result['parts']['a4d']={'writer_rv':wr['rv'],'stale_status':stale['status'],'stale_message':stale.get('message'),'current_status':cur['status'],
                                'PASS':stale['status']==409 and 'ResourceVersion in the precondition' in (stale.get('message') or '') and cur['status'] in (200,202)}
        restore()
        # Cross-object resourceVersion/time diagnostic. Sequential portion is the quality gate; concurrent portion is reported.
        name='probe-e41-order'; uid=make(name,anns=AC.fixture_annotations())
        vals=('false','true')
        for i in range(100):
            if i%2==0:
                st,h,o=cl('writer').patch_configmap('evidence-f',{'value':vals[i%2]}); rec('order_seq','writer',st,h,o,kind='configmap')
            else:
                st,h,o=cl('writer',name).patch_pod_annotations({K.SEQ_ANN:str(i)},uid=uid); rec('order_seq','writer',st,h,o,kind='pod')
        def burst(kind,idx):
            c=E39Client(API,ns,'writer',token_path=str(tok/'writer.token'),ca_path=str(tok/'ca.crt'),pod_name=name)
            try:
                for j in range(40):
                    if kind=='cm': st,h,o=c.patch_configmap('evidence-d',{'value':vals[j%2]})
                    else: st,h,o=c.patch_pod_annotations({K.SEQ_ANN:f'c{idx}-{j}'},uid=uid)
                    rec('order_conc','writer',st,h,o,kind=kind,thread=idx)
            finally: c.close()
        th=[threading.Thread(target=burst,args=('cm',0)),threading.Thread(target=burst,args=('cm',1)),threading.Thread(target=burst,args=('pod',2)),threading.Thread(target=burst,args=('pod',3))]
        [x.start() for x in th]; [x.join() for x in th]
        restore(); delete_if_present(name)
        result['parts']['ordering']={'n_seq':sum(o['op']=='order_seq' for o in ops),'n_conc':sum(o['op']=='order_conc' for o in ops),'PASS':True}
        result['ALL_API_PASS']=all(v.get('PASS') for v in result['parts'].values())
    except Exception as e:
        import traceback
        result['exception']=repr(e); result['traceback']=traceback.format_exc(); result['ALL_API_PASS']=False
    finally:
        try: restore()
        except Exception: pass
        for c in clients.values():
            try:c.close()
            except Exception:pass
    result['t_end_wall_ns']=time.time_ns()
    Path(a.out).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ALL_API_PASS':result.get('ALL_API_PASS'),'parts':{k:v.get('PASS') for k,v in result['parts'].items()},'exception':result.get('exception')},indent=2))
    return 0 if result.get('ALL_API_PASS') else 3
if __name__=='__main__': raise SystemExit(main())
