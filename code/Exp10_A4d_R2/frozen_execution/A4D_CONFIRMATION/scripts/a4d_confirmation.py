#!/usr/bin/env python3
"""A4d-only, audit-safe two-host confirmation wrapper over the locked E39 harness."""
from __future__ import annotations
import argparse,json,os,sys,time
from pathlib import Path
HERE=Path(__file__).resolve(); PKG=HERE.parents[2]; BASE=PKG/'shared'/'E39_LOCKED_BASE'; SRC=BASE/'src'
sys.path.insert(0,str(SRC)); sys.path.insert(0,str(PKG/'tools'))
import e39_kube as K  # noqa:E402
import e39_runner as R  # noqa:E402
from e39_kube import APIError,E39Client  # noqa:E402
from lock_util import verify_lock  # noqa:E402
API_BASE=os.environ.get('A4D_API_SERVER','https://127.0.0.1:6443')
CA_FILE=os.environ.get('A4D_CA_FILE','/var/lib/rancher/k3s/server/tls/server-ca.crt')
TOKEN_DIR=Path(os.environ.get('A4D_TOKEN_DIR',str(PKG/'runtime'/'tokens')))
WORKER_LABEL_KEY='a4d-role'; WORKER_LABEL_VALUE='workload'; EXPECTED_VERSION_PREFIX='v1.37'
R.ARMS_A=(R.A4D,)
R.ASYNC=('async_scope','async_app_policy','async_evidence_last_literal')
R.DELTAS_A=R.DELTAS_SHORT
R.FORBIDDEN_SEEDS=tuple(R.FORBIDDEN_SEEDS)+(93041271,93041272,40926017,972231175)
R.MIN_MEM_AVAILABLE_KB=768*1024; R.ABORT_MEM_AVAILABLE_KB=512*1024

def _create_target_pod_multihost(self,annotations=None,extra_labels=None,webhook=False,image='registry.k8s.io/pause:3.10'):
    labels={'app':'e39','e39-managed':'true'}; labels.update(extra_labels or {})
    if webhook: labels[K.WEBHOOK_LABEL[0]]=K.WEBHOOK_LABEL[1]
    md={'name':self.pod_name,'namespace':self.ns,'labels':labels}
    if annotations: md['annotations']={k:str(v) for k,v in annotations.items()}
    body={'apiVersion':'v1','kind':'Pod','metadata':md,'spec':{'restartPolicy':'Never','terminationGracePeriodSeconds':0,'automountServiceAccountToken':False,'enableServiceLinks':False,'nodeSelector':{WORKER_LABEL_KEY:WORKER_LABEL_VALUE},'containers':[{'name':'target','image':image,'imagePullPolicy':'IfNotPresent'}]}}
    return self.request('POST',f'{self._ns_root()}/pods',body)
K.E39Client.create_target_pod=_create_target_pod_multihost

def sha(p): return R.sha256_file(p)
def make_schedule(args):
    if args.seed is None: raise SystemExit('--seed is required')
    R.check_seed(args.seed)
    trials=R.build_stage1(args.seed,args.run_id,reps=args.reps,experiments=('A',))
    note='A4d audit-safe confirmation: A4d only x three invalidating conditions x {0,2,5,10,20,50,100,200} ms; no_write 39; uid_rebind_control 10; no top-up'
    if args.reps is not None: note+=f'; reps={args.reps} smoke only'
    doc=R.schedule_document(trials,1,args.seed,args.run_id,note=note)
    out=Path(args.make_schedule)
    if out.exists(): raise SystemExit(f'refusing to overwrite {out}')
    out.write_text(json.dumps(doc,indent=1)+'\n',encoding='utf-8')
    print(json.dumps({'n_trials':len(trials),'sha256':sha(out)},indent=2)); return 0

def mk_client(identity,namespace):
    return E39Client(API_BASE,namespace,identity,token_path=str(TOKEN_DIR/f'{identity}.token'),ca_path=CA_FILE)
def identity_probe(namespace):
    out={}
    for ident in ('responder','writer','reset','mirror'):
        c=mk_client(ident,namespace)
        try: out[ident]=c.whoami()
        finally: c.close()
    want={i:R.SA_PREFIX+i for i in out}; bad={i:{'got':out[i],'want':want[i]} for i in out if out[i]!=want[i]}
    if bad: raise APIError('identity mismatch: '+json.dumps(bad))
    return out

def run(args):
    doc,specs=R.load_schedule_file(args.schedule)
    verify_lock(args.lock,PKG,args.schedule,args.run_id,doc['schedule_seed'])
    if doc['run_id']!=args.run_id: raise APIError('schedule run_id mismatch')
    if any(s.get('arm')!=R.A4D for s in specs): raise APIError('non-A4d trial in A4d-only schedule')
    ns=R.NS_PREFIX+args.run_id; K.check_namespace(ns); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    logfh=open(out/'runner.log','a',encoding='utf-8'); who=identity_probe(ns)
    env=R.Env(base=API_BASE,namespace=ns,run_id=args.run_id,seed=doc['schedule_seed'],out_dir=out,experiments=['A'],token_dir=TOKEN_DIR,ca_file=CA_FILE,logfh=logfh)
    try:
        st,_,ver=env.reset.get_version(); gv=str((ver or {}).get('gitVersion',''))
        if st!=200 or not gv.startswith(EXPECTED_VERSION_PREFIX): raise APIError(f'unexpected Kubernetes version {gv!r}')
        session={'schema':'a4d.confirmation.session.v1','run_id':args.run_id,'schedule_seed':doc['schedule_seed'],'schedule_sha256':sha(args.schedule),'n_scheduled':len(specs),'api_server':API_BASE,'ca_sha256':sha(CA_FILE),'kubernetes_version':gv,'deployment':'two-host K3s; A4d target on a4d-worker; audit-safe stream required','worker_selector':{WORKER_LABEL_KEY:WORKER_LABEL_VALUE},'identities':who,'design':{'arms':[R.A4D],'conditions':list(R.ASYNC),'deltas_ms':list(R.DELTAS_A),'stage2_topup':False},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        (out/'SESSION_A4D_CONFIRMATION.json').write_text(json.dumps(session,indent=2)+'\n',encoding='utf-8')
        raw=out/'RAW_TRIALS.jsonl'; stats=R.run_trials(env,specs,raw,logfh=logfh,guards=True,stop_file=out/'STOP')
        (out/'RUN_STATS_A4D_CONFIRMATION.json').write_text(json.dumps(stats,indent=2)+'\n',encoding='utf-8')
        return 0 if not stats.get('aborted') else 3
    finally: env.close(); logfh.close()

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--make-schedule'); ap.add_argument('--run',action='store_true'); ap.add_argument('--seed',type=int); ap.add_argument('--reps',type=int); ap.add_argument('--run-id',required=True); ap.add_argument('--schedule'); ap.add_argument('--output-dir'); ap.add_argument('--lock')
    a=ap.parse_args(argv)
    if a.make_schedule: return make_schedule(a)
    if a.run:
        if not a.schedule or not a.output_dir or not a.lock: ap.error('--run requires --schedule --output-dir --lock')
        return run(a)
    ap.error('choose --make-schedule or --run')
if __name__=='__main__': raise SystemExit(main())
