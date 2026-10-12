#!/usr/bin/env python3
"""E38 prospective engineering runner for a specifically named disposable kind cluster.

NOT YET TESTED AGAINST LIVE KUBERNETES in the authoring environment. First run
--dry-run, preflight.py, then --phase smoke on a dedicated non-production VM.
Never points at a cluster unless operator explicitly acknowledges destructive
Pod and ConfigMap changes; creates/uses only run-labeled e38-* namespace.
Only a separate signed lock enables confirmatory execution.
"""
from __future__ import annotations
import argparse, hashlib, json, os, random, re, subprocess, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from e38_model import FLAGS, frozen_planner_certificate, model_policy, make_hybrid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
NAME='e38-target'
CM='obs-'
RESOURCE_CM=('scope-policy','application-policy','e38-epoch','obs-irrelevant')
METHODS=('DCJI','FullRefresh','TTLGuard','NativeUID')
PLAN=frozen_planner_certificate()  # planned at queue construction, outside timed acquisition
CONDITIONS=(
    'stable','irrelevant','evidence_cut','uid_rebind','scope_expand',
    'scope_shrink','application_policy_revoke','during_acquisition','unknown_open_epoch',
    'postgate_uid','postgate_evidence','postgate_scope','postgate_application_policy',
    'symmetric_bdf_stress'
)
PRIMARY=('stable','irrelevant','evidence_cut','uid_rebind','scope_expand','during_acquisition','postgate_uid','postgate_evidence','postgate_scope')
PHASES=('smoke','pilot','confirmatory')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def source_tree_sha():
    '''Hash every executable source and contract in src; exclude Python bytecode.'''
    h=hashlib.sha256()
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.json'):
            h.update(str(path.relative_to(HERE)).encode()+b'\0'+sha(path).encode()+b'\n')
    # The actual model consumes a JSON input OUTSIDE src; hash it too.
    # Otherwise an operator could change the threat model after pilot without
    # triggering the source-bound phase acceptance gate.
    provenance=ROOT/'source_provenance/E37_F5_PHYS_ORIGINAL.json'
    h.update(b'source_provenance/E37_F5_PHYS_ORIGINAL.json\0'+sha(provenance).encode()+b'\n')
    return h.hexdigest()
def monotonic_ms():return time.monotonic_ns()/1e6
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':')).encode()

class APIError(RuntimeError):pass

class KubeAPI:
    def __init__(self,base,namespace):
        parsed=urlsplit(base)
        if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.port is None
                or parsed.username or parsed.password or parsed.path not in ('','/')
                or parsed.query or parsed.fragment or parsed.netloc!=f'127.0.0.1:{parsed.port}'):
            raise ValueError('Only an exact http://127.0.0.1:PORT loopback proxy URL is supported')
        if not namespace.startswith('e38-') or len(namespace)>63:
            raise ValueError('Namespace must be disposable and prefixed e38-')
        self.base=base.rstrip('/');self.ns=namespace;self.requests=[];self.events=[]
    def url(self,kind,name=None):
        root=f'/api/v1/namespaces/{self.ns}'
        if kind=='ns':return '/api/v1/namespaces'+('/'+name if name else '')
        suffix={'cm':'configmaps','pod':'pods'}[kind]
        return root+'/'+suffix+('/'+name if name else '')
    def call(self,method,path,body=None,kind='setup'):
        if method in ('POST','PUT','PATCH','DELETE'):
            ns_root=f'/api/v1/namespaces/{self.ns}'
            allowed_cms={f'{ns_root}/configmaps/{x}' for x in
                         tuple('obs-'+k for k in FLAGS)+RESOURCE_CM}
            allowed=False
            if method=='POST' and path=='/api/v1/namespaces':
                md=(body or {}).get('metadata',{})
                allowed=md.get('name')==self.ns and md.get('labels',{}).get('e38-managed')=='true'
            elif method=='POST' and path=='/apis/authorization.k8s.io/v1/selfsubjectaccessreviews':
                allowed=(body or {}).get('spec',{}).get('resourceAttributes',{}).get('namespace')==self.ns
            elif method=='POST' and path==f'{ns_root}/pods':
                md=(body or {}).get('metadata',{})
                allowed=md.get('name')==NAME and md.get('labels',{}).get('e38-managed')=='true'
            elif method=='POST' and path==f'{ns_root}/configmaps':
                md=(body or {}).get('metadata',{})
                allowed=(md.get('name') in {x.rsplit('/',1)[-1] for x in allowed_cms}
                         and md.get('labels',{}).get('e38-managed')=='true')
            elif method=='PATCH' and path in allowed_cms:
                allowed=isinstance((body or {}).get('data'),dict)
            elif method=='DELETE' and path==f'{ns_root}/pods/{NAME}':
                allowed=bool((body or {}).get('preconditions',{}).get('uid'))
            if not allowed:raise APIError('WRITE BLOCKED: unapproved resource, operation or UID precondition')
        payload=canonical(body) if body is not None else None
        headers={'Content-Type':'application/merge-patch+json' if method=='PATCH' else 'application/json'}
        req=Request(self.base+path,data=payload,method=method,headers=headers)
        t0=monotonic_ms()
        try:
            with urlopen(req,timeout=20) as res: raw=res.read();status=res.status
        except HTTPError as err:
            raw=err.read();status=err.code
        except (URLError,TimeoutError) as exc:
            self.requests.append({'kind':kind,'method':method,'path':path,'t0_ms':t0,
                't1_ms':monotonic_ms(),'status':None,'error':repr(exc),'payload_sha256':hashlib.sha256(payload or b'').hexdigest()})
            raise APIError(f'{method} {path}: {exc}') from exc
        record={'kind':kind,'method':method,'path':path,'t0_ms':t0,'t1_ms':monotonic_ms(),
                'status':status,'response_bytes':len(raw)}
        if payload is not None:record.update(payload_sha256=hashlib.sha256(payload).hexdigest(),payload_bytes=len(payload))
        if method=='DELETE':record['delete_options']=body
        self.requests.append(record)
        try:return json.loads(raw) if raw else None,status,len(raw)
        except ValueError:raise APIError('Non-JSON API reply for '+method+' '+path)
    def expect(self,method,path,body=None,ok=(200,201,202),kind='setup'):
        data,status,size=self.call(method,path,body,kind)
        if status not in ok:raise APIError(f'{method} {path}: HTTP {status}: {str(data)[:240]}')
        return data,size
    def get_cm(self,name,kind='guard'):
        obj,n=self.expect('GET',self.url('cm',name),kind=kind)
        self.requests[-1].update(resource_uid=obj.get('metadata',{}).get('uid'),
            resource_version=obj.get('metadata',{}).get('resourceVersion'),
            decoded_data_sha256=hashlib.sha256(canonical(obj.get('data',{}))).hexdigest())
        return obj,n
    def get_pod(self,kind='guard',allow_missing=False):
        data,status,n=self.call('GET',self.url('pod',NAME),kind=kind)
        if status==404 and allow_missing:return None,n
        if status!=200:raise APIError(f'GET pod: HTTP {status} {data}')
        self.requests[-1].update(resource_uid=data.get('metadata',{}).get('uid'),
            resource_version=data.get('metadata',{}).get('resourceVersion'),
            pod_phase=data.get('status',{}).get('phase'))
        return data,n
    def patch_cm(self,name,data,kind='mutation'):
        return self.expect('PATCH',self.url('cm',name),{'data':{k:str(v) for k,v in data.items()}},kind=kind)[0]
    def ssar(self,kind='guard'):
        body={'apiVersion':'authorization.k8s.io/v1','kind':'SelfSubjectAccessReview',
          'spec':{'resourceAttributes':{'namespace':self.ns,'verb':'delete','group':'','resource':'pods','name':NAME}}}
        data,_=self.expect('POST','/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',body,kind=kind)
        allowed=data['status'].get('allowed') is True
        self.requests[-1]['authorization_allowed']=allowed
        return allowed
    def delete_pod(self,uid,kind='guard'):
        if not uid or not isinstance(uid,str):raise APIError('No UID for conditioned delete')
        opts={'apiVersion':'v1','kind':'DeleteOptions','gracePeriodSeconds':0,
              'preconditions':{'uid':uid}}
        return self.call('DELETE',self.url('pod',NAME),opts,kind=kind)
    def get_epoch(self):
        obj,n=self.get_cm('e38-epoch');return int(obj['data']['sequence']),n

def pod_ready(p):
    return bool(p and p.get('status',{}).get('phase')=='Running' and any(
      c.get('type')=='Ready' and c.get('status')=='True' for c in p.get('status',{}).get('conditions',[])))

def read_until(api,predicate,timeout=90,period=0.2):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        obj,_=api.get_pod(kind='setup',allow_missing=True)
        if predicate(obj):return obj
        time.sleep(period)
    raise APIError('Timed out waiting for Pod state')

def create_pod(api,image):
    manifest={'apiVersion':'v1','kind':'Pod','metadata':{
        'name':NAME,'namespace':api.ns,'labels':{'app':'e38','e38-managed':'true'}},
        'spec':{'restartPolicy':'Never','containers':[{'name':'web','image':image,'imagePullPolicy':'IfNotPresent',
        'ports':[{'containerPort':80}], 'readinessProbe':{'httpGet':{'path':'/','port':80},
        'periodSeconds':1,'initialDelaySeconds':1,'timeoutSeconds':1,'failureThreshold':20}}]}}
    api.expect('POST',api.url('pod'),manifest,kind='setup')
    return read_until(api,pod_ready)

def ensure_owned_namespace(api,run_id):
    ns,status,_=api.call('GET',api.url('ns',api.ns),kind='preflight')
    if status==404:
        ns,_=api.expect('POST','/api/v1/namespaces',{'apiVersion':'v1','kind':'Namespace',
         'metadata':{'name':api.ns,'labels':{'e38-managed':'true','e38-run-id':run_id}}},kind='setup')
    elif status!=200:raise APIError('Namespace lookup failure: HTTP '+str(status))
    labels=ns['metadata'].get('labels',{})
    if labels.get('e38-managed')!='true' or labels.get('e38-run-id')!=run_id:
        raise APIError('Namespace ownership mismatch: will not write into it')
    return ns

def ensure_fixture(api,image):
    for k in ('a','b','c','d','e','f'):
        name='obs-'+k
        d,s,_=api.call('GET',api.url('cm',name),kind='setup')
        if s==404:api.expect('POST',api.url('cm'),{'apiVersion':'v1','kind':'ConfigMap',
            'metadata':{'name':name,'namespace':api.ns,'labels':{'e38-managed':'true'}},
            'data':{'valid':'true'}},kind='setup')
        elif s!=200 or d['metadata'].get('labels',{}).get('e38-managed')!='true':raise APIError('Unowned CM '+name)
    data={'scope-policy':{'members':json.dumps([NAME])},
      'application-policy':{'allowed':'true'}, 'e38-epoch':{'sequence':'0'},
      'obs-irrelevant':{'note':'original'}}
    for name,kv in data.items():
        d,s,_=api.call('GET',api.url('cm',name),kind='setup')
        if s==404:api.expect('POST',api.url('cm'),{'apiVersion':'v1','kind':'ConfigMap',
            'metadata':{'name':name,'namespace':api.ns,'labels':{'e38-managed':'true'}},
            'data':kv},kind='setup')
        elif s!=200 or d['metadata'].get('labels',{}).get('e38-managed')!='true':raise APIError('Unowned CM '+name)
    p,_=api.get_pod(kind='setup',allow_missing=True)
    if p is not None and p.get('metadata',{}).get('labels',{}).get('e38-managed')!='true':
        raise APIError('Unowned Pod with target name: refuse to delete')
    return data

def reset_fixture(api,image):
    # Only objects verified to be in a run-labeled, disposable namespace.
    p,_=api.get_pod(kind='setup',allow_missing=True)
    if p is not None:
        if p.get('metadata',{}).get('labels',{}).get('e38-managed')!='true':
            raise APIError('Reset refused: target Pod lacks e38-managed ownership label')
        uid=p['metadata']['uid'];data,status,_=api.delete_pod(uid,kind='reset')
        if status not in (200,202):raise APIError(f'Reset DELETE failed HTTP {status}: {data}')
        read_until(api,lambda x:x is None,timeout=90)
    for k in FLAGS:api.patch_cm('obs-'+k,{'valid':'true'},kind='reset')
    api.patch_cm('scope-policy',{'members':json.dumps([NAME])},kind='reset')
    api.patch_cm('application-policy',{'allowed':'true'},kind='reset')
    api.patch_cm('e38-epoch',{'sequence':'0'},kind='reset')
    api.patch_cm('obs-irrelevant',{'note':'original'},kind='reset')
    p=create_pod(api,image)
    return p['metadata']['uid']


def mutate(api,kind,uid,image,event_history):
    """All E38-controlled evidence/scope/policy mutation goes through epoch fence.
    This does NOT fence arbitrary external writers and cannot enforce atomicity
    between gate and DELETE. Post-gate conditions intentionally demonstrate this.
    """
    event={'kind':kind,'started_ms':monotonic_ms(),'original_uid':uid,'completed':False}
    event_history.append(event)
    if kind=='irrelevant':
        api.patch_cm('obs-irrelevant',{'note':'changed'});event['completed']=True;event['finished_ms']=monotonic_ms();return
    if kind=='uid_rebind':
        old,_=api.get_pod(kind='mutation',allow_missing=True)
        if old is None or old['metadata']['uid']!=uid:raise APIError('Cannot rebind: old UID differs')
        if old.get('metadata',{}).get('labels',{}).get('e38-managed')!='true':
            raise APIError('Rebinding refused: Pod ownership label missing')
        _,status,_=api.delete_pod(uid,kind='mutation')
        if status not in (200,202):raise APIError('Mutation UID delete failed '+str(status))
        read_until(api,lambda x:x is None,timeout=90)
        new=create_pod(api,image)
        if new['metadata']['uid']==uid:raise APIError('Recreated Pod has unchanged UID')
        event.update(completed=True,replacement_uid=new['metadata']['uid'],finished_ms=monotonic_ms());return
    seq_obj,_=api.get_cm('e38-epoch',kind='mutation')
    seq=int(seq_obj['data']['sequence'])
    if seq%2:raise APIError('Cannot begin mutation: epoch already open')
    api.patch_cm('e38-epoch',{'sequence':str(seq+1)})
    event['open_sequence']=seq+1
    if kind=='unknown_open_epoch':
        # Deliberately leave odd to force an UNKNOWN acquisition. Reset before next trial.
        event['completed']=True;event['finished_ms']=monotonic_ms();return
    try:
        if kind=='evidence_cut':
            for k in ('a','c','e'):api.patch_cm('obs-'+k,{'valid':'false'})
        elif kind=='symmetric_bdf_stress':
            for k in ('b','d','f'):api.patch_cm('obs-'+k,{'valid':'false'})
        elif kind=='scope_expand':api.patch_cm('scope-policy',{'members':json.dumps([NAME,'benign'])})
        elif kind=='scope_shrink':api.patch_cm('scope-policy',{'members':'[]'})
        elif kind=='application_policy_revoke':api.patch_cm('application-policy',{'allowed':'false'})
        else:raise ValueError('Unknown mutation '+kind)
    finally:
        # Always close the epoch if possible. Failure remains a logged trial error.
        api.patch_cm('e38-epoch',{'sequence':str(seq+2)})
    event.update(completed=True,finished_ms=monotonic_ms(),closed_sequence=seq+2)


def get_oracle(api,uid):
    # Independent complete physical state read outside timed guard; no DCJI import.
    evidence={}
    for k in FLAGS:
        obj,_=api.get_cm('obs-'+k,kind='oracle');evidence[k]=obj['data']['valid']=='true'
    obj,_=api.get_cm('scope-policy',kind='oracle')
    members=json.loads(obj['data']['members']);scope=(members==[NAME])
    obj,_=api.get_cm('application-policy',kind='oracle');app=(obj['data']['allowed']=='true')
    pod,_=api.get_pod(kind='oracle',allow_missing=True)
    rbac=api.ssar(kind='oracle')
    result=model_policy(evidence,bool(pod and pod['metadata']['uid']==uid),scope,app,rbac,pod_ready(pod))
    result['physical_evidence']=evidence;result['physical_scope_members']=members
    result['physical_uid']=pod['metadata']['uid'] if pod else None
    return result


def guard(api,method,uid,mode,ttl_seconds,image,condition,event_history,queued_ms=None):
    """One measured guard. Decision is hybrid-state evaluation, not oracle output.
    Hardware GET records and timing come only from 'guard' request slice.
    """
    t0=monotonic_ms();start=len(api.requests)
    if method=='TTLGuard' and ttl_seconds<=0:raise APIError('TTLGuard needs positive, fixed TTL')
    cache_age_s=((t0-queued_ms)/1000 if queued_ms is not None else 0.)
    ttl_expired=(method=='TTLGuard' and cache_age_s>=ttl_seconds)
    full_refresh=(method=='FullRefresh' or ttl_expired)
    fenced=(method=='DCJI' or full_refresh)
    selected=PLAN['observations'] if method=='DCJI' else (tuple(FLAGS)+('uid','scope') if full_refresh else ('uid',))
    requested=tuple(k for k in selected if k in FLAGS)
    # NativeUID is a limited-invariant diagnostic, not a like-for-like J/scope baseline.
    recorded={};modes={'parallel','sequential'}
    if mode not in modes:raise ValueError('Unknown acquisition mode')
    epoch_before=None;epoch_after=None;coherent=False
    if fenced:
        epoch_before,_=api.get_epoch()
        if epoch_before%2:
            raise APIError('OPEN_EPOCH_DETECTED: odd initial epoch; abort without deletion')
    # This helper is intentionally declared after epoch check to avoid optimistic
    # return on a malformed fixture; normal returns use the final block below.
    def read_flag(k):
        obj,_=api.get_cm('obs-'+k)
        value=obj.get('data',{}).get('valid')
        if value not in ('true','false'):raise APIError('Unknown flag '+k)
        return k,value=='true'
    if mode=='parallel' and requested:
        if condition=='during_acquisition':raise APIError('during_acquisition requires sequential mode')
        with ThreadPoolExecutor(max_workers=len(requested)) as pool:
            for k,v in pool.map(read_flag,requested):recorded[k]=v
    else:
        for i,k in enumerate(requested):
            k,v=read_flag(k);recorded[k]=v
            if i==0 and condition=='during_acquisition':mutate(api,'evidence_cut',uid,image,event_history)
    pod,_=api.get_pod()
    obj,_=api.get_cm('application-policy')
    app_policy=obj['data'].get('allowed')=='true'
    if fenced:
        scope_obj,_=api.get_cm('scope-policy')
        scope=(json.loads(scope_obj['data']['members'])==[NAME])
        epoch_after,_=api.get_epoch()
        coherent=epoch_before==epoch_after and epoch_before%2==0
    else:
        scope=True # stale approved singleton, intentionally not reacquired
    get_finish=monotonic_ms()
    rbac=api.ssar()
    evidence=make_hybrid({k:True for k in FLAGS},recorded)
    # The TTLGuard uses a fixed fresh-on-queue cached policy; no claim is made
    # about elapsed hardware freshness beyond the declared TTL semantics.
    uid_match=bool(pod and pod['metadata']['uid']==uid)
    truth=model_policy(evidence,uid_match,scope,app_policy,rbac,pod_ready(pod))
    if fenced and not coherent:
        decision='UNKNOWN_COHERENCE';reason='fence changed or open'
    else:
        decision=truth['decision'];reason='evaluated observed/cached predicates'
    t1=monotonic_ms();records=api.requests[start:]
    guard_gets=[x for x in records if x['kind']=='guard' and x['method']=='GET']
    return {'decision':decision,'reason':reason,'model':truth,'coherent':coherent,
      'partial_invariant_method':method=='NativeUID' or (method=='TTLGuard' and not ttl_expired),
      'ttl_expired':ttl_expired if method=='TTLGuard' else None,'cache_age_s':cache_age_s if method=='TTLGuard' else None,
      'epoch_before':epoch_before,'epoch_after':epoch_after,
      'measured_evidence':recorded,'queried_evidence':list(requested),'selected_certificate':list(selected),
      'observed_uid':pod['metadata']['uid'] if pod else None,
      'guard_get_count':len(guard_gets),'core_evidence_get_count':sum('obs-' in x['path'] for x in guard_gets),
      'response_body_bytes':sum(x.get('response_bytes',0) for x in guard_gets),
      'refresh_latency_ms':get_finish-t0,'gate_latency_ms':t1-t0,
      'ssar_posts':sum(x['method']=='POST' and 'selfsubjectaccessreviews' in x['path'] for x in records),
      'guard_requests':records}


def verify_effect(api,decision_uid,status,delete_sent):
    if not delete_sent:return {'pod_absent':False,'effect_verified':False,'post_effect_uid':None,'effect_elapsed_ms':None}
    if status not in (200,202):
        pod,_=api.get_pod(kind='effect',allow_missing=True)
        return {'pod_absent':pod is None,'effect_verified':False,
                'post_effect_uid':pod['metadata']['uid'] if pod else None,'effect_elapsed_ms':None}
    t0=monotonic_ms()
    end=time.monotonic()+30
    while time.monotonic()<end:
        pod,_=api.get_pod(kind='effect',allow_missing=True)
        if pod is None:return {'pod_absent':True,'effect_verified':True,
                              'post_effect_uid':None,'effect_elapsed_ms':monotonic_ms()-t0}
        if pod['metadata']['uid']!=decision_uid:
            return {'pod_absent':False,'effect_verified':False,'post_effect_uid':pod['metadata']['uid'],
                    'effect_elapsed_ms':None}
        time.sleep(.1)
    return {'pod_absent':False,'effect_verified':False,'post_effect_uid':decision_uid,'effect_elapsed_ms':None}


def oracle_after_controlled_mutation(prior,mutation,events):
    """Prospective use-time truth based on the prior physically read state plus
    independently logged successful injector calls. Does not presume immunity
    to uninstrumented external writers; after-action records need separate audit.
    """
    if not mutation:return dict(prior,oracle_source='preacquisition physical oracle and no new injector')
    if not events or not events[-1].get('completed') or events[-1]['kind']!=mutation:
        raise APIError('Missing completed injector event; oracle unavailable')
    out=dict(prior);ev=dict(prior['physical_evidence'])
    if mutation=='uid_rebind':out['physical_uid']=events[-1]['replacement_uid']
    elif mutation=='evidence_cut':
        for k in ('a','c','e'):ev[k]=False
    elif mutation=='scope_expand':out['physical_scope_members']=[NAME,'benign']
    elif mutation=='application_policy_revoke':out['app_policy_allowed']=False
    else:raise ValueError('Unknown oracle mutation '+mutation)
    truth=model_policy(ev, False if mutation=='uid_rebind' else out['same_binding'],
        out['physical_scope_members']==[NAME],
        out['app_policy_allowed'],out['rbac_allowed'],out['ready'])
    out.update(truth,physical_evidence=ev,oracle_source='controlled injector event and pre-state; no global transaction')
    return out

class BackgroundTraffic:
    """Bounded unrelated 20 GET/s local background API load (not CPU saturation)."""
    def __init__(self,api,rate=20):
        self.api=api;self.rate=rate;self.stop=threading.Event();self.issued=0;self.errors=[];self.thread=None
    def start(self):
        def work():
            while not self.stop.is_set():
                try:
                    self.api.get_cm('obs-irrelevant',kind='background_load');self.issued+=1
                except Exception as exc:self.errors.append(repr(exc))
                self.stop.wait(1/self.rate)
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
    def finish(self):
        if self.thread:
            self.stop.set();self.thread.join(timeout=20)
            if self.thread.is_alive():raise APIError('Background load thread did not stop')
        return {'issued':self.issued,'errors':self.errors,'target_rps':self.rate}

def one_trial(api,method,condition,image,mode,ttl,horizon,trial_id,phase,block,seed,order,rep_index=0,load='idle'):
    trial={'trial_id':trial_id,'method':method,'condition':condition,'phase':phase,'block_id':block,
           'seed':seed,'rep_index':rep_index,'method_order':order,'namespace':api.ns,'mode':mode,'load':load,'status':'STARTED',
           'failed':False,'timeout':False,'safety_notes':[],'events':[]}
    start=monotonic_ms();logs=len(api.requests)
    try:
        uid=reset_fixture(api,image)
        queued_ms=monotonic_ms()
        trial['decision_uid']=uid
        pre={'irrelevant':'irrelevant','evidence_cut':'evidence_cut','uid_rebind':'uid_rebind',
          'scope_expand':'scope_expand','scope_shrink':'scope_shrink',
          'application_policy_revoke':'application_policy_revoke',
          'symmetric_bdf_stress':'symmetric_bdf_stress','unknown_open_epoch':'unknown_open_epoch'}
        if condition in pre:mutate(api,pre[condition],uid,image,trial['events'])
        trial['oracle_at_gate_preacquisition']=get_oracle(api,uid)
        bg=BackgroundTraffic(api) if load=='moderate' else None
        if bg:bg.start();time.sleep(.05)
        decision_started=monotonic_ms()
        try:
            try:g=guard(api,method,uid,mode,ttl,image,condition,trial['events'],queued_ms=queued_ms)
            finally:
                if bg:trial['background_load']=bg.finish()
        except APIError as e:
            if str(e).startswith('OPEN_EPOCH_DETECTED'):
                trial.update(guard={'decision':'UNKNOWN_COHERENCE','reason':str(e)},
                    guard_decision='UNKNOWN_COHERENCE',status='UNKNOWN',deleted=False,
                    oracle_at_use=get_oracle(api,uid),delete_sent=False,server_http_status=None,
                    effect={'pod_absent':False,'effect_verified':False,'post_effect_uid':None,'effect_elapsed_ms':None},
                    gate_deadline_missed=None,effect_deadline_missed=None)
                return trial
            raise
        trial.update(guard=g,guard_decision=g['decision'],oracle_gate_decision=trial['oracle_at_gate_preacquisition']['decision'])
        if condition=='during_acquisition':trial['oracle_gate_decision']=get_oracle(api,uid)['decision']
        if g['decision']=='EXECUTE':
            post={'postgate_uid':'uid_rebind','postgate_evidence':'evidence_cut',
              'postgate_scope':'scope_expand','postgate_application_policy':'application_policy_revoke'}
            if condition in post:mutate(api,post[condition],uid,image,trial['events'])
            # No oracle GET or SSAR intervenes between accepted gate and DELETE;
            # use-time truth uses independently logged controlled mutation events.
            trial['oracle_at_use']=oracle_after_controlled_mutation(
              trial['oracle_at_gate_preacquisition'],post.get(condition),trial['events'])
            tdel=monotonic_ms()
            body,status,_=api.delete_pod(uid)
            effect=verify_effect(api,uid,status,True)
            trial.update(delete_sent=True,server_http_status=status,delete_options={'preconditions':{'uid':uid}},
              effect=effect,action_dispatch_ms=tdel,deleted=effect['effect_verified'])
            if status==409 and effect['effect_verified']:trial['safety_notes'].append('ERROR 409 miscounted effect')
            if effect['post_effect_uid'] and effect['post_effect_uid']!=uid and status in (200,202):
                trial['safety_notes'].append('Wrong-target deletion may have occurred: investigate immediately')
            if trial['oracle_at_use']['decision']!='EXECUTE' and status in (200,202):
                trial['safety_notes'].append('USE-TIME INVALID ACTION ACCEPTED: UID comparator does not enforce other policy predicates')
        else:
            trial['oracle_at_use']=get_oracle(api,uid)
            trial.update(delete_sent=False,server_http_status=None,deleted=False,
                effect={'pod_absent':False,'effect_verified':False,'post_effect_uid':None,'effect_elapsed_ms':None})
        trial['decision_to_gate_ms']=g.get('gate_latency_ms')
        trial['decision_to_first_effect_ms']=(monotonic_ms()-decision_started if trial['effect']['effect_verified'] else None)
        trial['gate_deadline_missed']=g.get('gate_latency_ms',float('inf'))>horizon*1000
        # Effect latency is not applicable to an intentionally aborted decision
        # or a server-side 409 that correctly prevents the action.  Counting
        # these safe negative cases as deadline misses inflates failure rates.
        trial['effect_deadline_missed']=(
            (trial['decision_to_first_effect_ms'] is None or trial['decision_to_first_effect_ms']>horizon*1000)
            if trial.get('server_http_status') in (200,202) else None)
        trial['status']='COMPLETE_WITH_SAFETY_NOTE' if trial['safety_notes'] else 'COMPLETE'
    except Exception as exc:
        trial.update(status='ERROR',failed=True,timeout=isinstance(exc,TimeoutError) or 'timed out' in repr(exc).lower(),error=repr(exc))
    finally:
        trial['elapsed_wall_ms']=monotonic_ms()-start
        trial['all_requests']=api.requests[logs:]
        trial['all_request_count']=len(trial['all_requests'])
    return trial


def verify_cluster_context(expected='kind-e38'):
    result=subprocess.run(['kubectl','config','current-context'],capture_output=True,text=True,timeout=10)
    current=result.stdout.strip()
    if result.returncode or current!=expected:raise APIError(f'Context mismatch: need {expected}, got {current!r}')
    return current

def validate_phase_report(path,expected_phase):
    """Tie an operator acceptance to preserved raw bytes, schedule and tested code.

    A JSON boolean is NOT sufficient evidence of an actual completed smoke/pilot.
    This only verifies local provenance and consistency, not the human's signature.
    """
    report=json.loads(Path(path).read_text())
    rid=report.get('run_id')
    if not isinstance(rid,str) or not rid.startswith('e38') or not rid.isalnum():
        raise APIError('Acceptance report has an invalid or absent run ID')
    if report.get('phase')!=expected_phase:raise APIError('Acceptance phase mismatch')
    run=ROOT/'results'/f'{rid}-{expected_phase}'
    raw=run/'RAW_TRIALS.jsonl'
    if not raw.is_file() or not (run/'SESSION.json').is_file():
        raise APIError('Acceptance has no locally retained physical trial records: '+str(run))
    session=json.loads((run/'SESSION.json').read_text())
    if session.get('phase')!=expected_phase or session.get('run_id')!=rid:
        raise APIError('Acceptance run metadata mismatch')
    if session.get('source_tree_sha256')!=source_tree_sha():
        raise APIError('Source changed since approved '+expected_phase+'; re-test affected phase')
    if report.get('raw_sha256')!=sha(raw):
        raise APIError('Acceptance raw SHA256 not equal to existing raw results')
    descriptive=run/'DESCRIPTIVE_AUDIT.json'
    integrity=run/'RAW_INTEGRITY_REPORT.json'
    if not descriptive.is_file() or not integrity.is_file():
        raise APIError('Missing required full-run audit files for '+expected_phase)
    analysis=json.loads(descriptive.read_text());checks=json.loads(integrity.read_text())
    if not analysis.get('complete_schedule') or analysis.get('raw_sha256')!=sha(raw):
        raise APIError('Incomplete or stale descriptive audit for '+expected_phase)
    if checks.get('hard_accounting_errors') or checks.get('total_attempts_checked')!=session.get('total_planned_trials'):
        raise APIError('Raw integrity problems in '+expected_phase)
    if analysis.get('failed_attempts'):
        raise APIError('Failed attempts require engineering re-review of '+expected_phase)
    # Recompute the raw audit at the phase boundary; do not trust an arbitrary
    # previously written JSON claiming zero errors.
    import importlib.util
    spec=importlib.util.spec_from_file_location('e38_boundary_verify_raw',ROOT/'analysis/verify_raw.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    rows=[json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
    recomputed=verifier.audit(rows,session)
    if recomputed['hard_accounting_errors'] or len(rows)!=session['total_planned_trials']:
        raise APIError('Physical raw trials fail a fresh phase-boundary integrity audit')
    if recomputed['total_attempts_checked']!=checks.get('total_attempts_checked'):
        raise APIError('Saved raw audit disagrees with fresh recomputation')
    return report


def require_lock(path,conditions,methods,mode,repetitions,blocks,image,ttl_seconds,horizon_seconds,seed):
    """Second-stage confirmatory gate. A real independent signoff cannot be self-certified by code."""
    data=json.loads(Path(path).read_text())
    expected={'status':'LOCKED_APPROVED','independent_oracle_approval':True,
       'namespace_disposable_approval':True,'risk_signoff':True,'pilot_independent_from_confirmatory':True}
    for key,value in expected.items():
        if data.get(key)!=value:raise APIError('Confirmatory lock incomplete: '+key)
    for key in ('operator','hardware_provenance','protocol_sha256','source_sha256','pilot_report_sha256',
       'oracle_approval_record','analysis_plan_sha256','sample_size_justification','lock_timestamp_utc',
       'source_tree_sha256','oracle_spec_sha256','trial_contract_sha256','raw_audit_sha256'):
        if not data.get(key):raise APIError('Lock missing '+key)
    if data['source_sha256']!=sha(__file__):raise APIError('Locked source hash mismatch')
    if data['source_tree_sha256']!=source_tree_sha():raise APIError('Locked full source tree hash mismatch')
    if data['oracle_spec_sha256']!=sha(ROOT/'protocol/E38_ORACLE_REVIEW_FOR_REVIEWERS.md'):
        raise APIError('Locked E38 oracle specification hash mismatch')
    if data['trial_contract_sha256']!=sha(ROOT/'protocol/E38_TRIAL_CONTRACT_V2.json'):
        raise APIError('Locked trial contract hash mismatch')
    if data['raw_audit_sha256']!=sha(ROOT/'analysis/verify_raw.py'):
        raise APIError('Locked raw audit script hash mismatch')
    if data['protocol_sha256']!=sha(ROOT/'protocol/CONFIRMATORY_DESIGN.json'):
        raise APIError('Protocol hash mismatch')
    if data['analysis_plan_sha256']!=sha(ROOT/'analysis/analyze.py'):
        raise APIError('Analysis script hash mismatch')
    if data.get('image')!=image or not re.fullmatch(r'[^\s@]+@sha256:[0-9a-fA-F]{64}',image):
        raise APIError('Confirmatory image must match an exact locked SHA256 digest')
    if sorted(data['conditions'])!=sorted(conditions) or sorted(data['methods'])!=sorted(methods):
        raise APIError('Lock condition/method mismatch')
    if data['mode']!=mode or data['repetitions_per_method_condition']!=repetitions or data['blocks']!=blocks:
        raise APIError('Lock sample schedule mismatch')
    if (data.get('ttl_seconds')!=ttl_seconds or data.get('horizon_seconds')!=horizon_seconds
            or data.get('random_seed')!=seed):
        raise APIError('Lock TTL/horizon/random-seed mismatch')
    return data


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--phase',choices=PHASES,default='smoke')
    ap.add_argument('--proxy',default='http://127.0.0.1:18038')
    ap.add_argument('--context',default='kind-e38')
    ap.add_argument('--namespace',required=True,help='e38-* owned disposable namespace')
    ap.add_argument('--run-id',required=True,help='fixed run label for resuming an existing namespace')
    ap.add_argument('--image',default='nginx:stable-alpine')
    ap.add_argument('--conditions',default=','.join(PRIMARY))
    ap.add_argument('--methods',default='DCJI,FullRefresh')
    ap.add_argument('--mode',choices=['sequential','parallel'],default='sequential')
    ap.add_argument('--load',choices=['idle','moderate'],default='idle')
    ap.add_argument('--repetitions',type=int,default=None)
    ap.add_argument('--blocks',type=int,default=1)
    ap.add_argument('--seed',type=int,default=3801)
    ap.add_argument('--ttl-seconds',type=float,default=30.0)
    ap.add_argument('--horizon-seconds',type=float,default=30.0)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--final-lock',type=Path)
    ap.add_argument('--smoke-report',type=Path,help='Audited smoke report, required for pilot/confirmatory')
    ap.add_argument('--pilot-report',type=Path,help='Audited pilot report, required for confirmatory')
    ap.add_argument('--ack-disposable',action='store_true')
    ap.add_argument('--dry-run',action='store_true')
    a=ap.parse_args(argv)
    conditions=[x.strip() for x in a.conditions.split(',') if x.strip()]
    methods=[x.strip() for x in a.methods.split(',') if x.strip()]
    if not conditions or len(conditions)!=len(set(conditions)):
        ap.error('Require nonempty distinct conditions; repeated cells corrupt matching')
    if len(methods)!=len(set(methods)):
        ap.error('Require distinct methods; repeated cells corrupt matching')
    unknown=set(conditions)-set(CONDITIONS)
    if unknown:ap.error('Invalid conditions: '+str(unknown))
    if a.context!='kind-e38':ap.error('Only dedicated kind-e38 cluster is permitted')
    if a.ttl_seconds<=0 or a.horizon_seconds<=0:ap.error('Positive TTL and horizon required')
    if not methods or set(methods)-set(METHODS):ap.error('Invalid methods')
    if a.mode=='parallel' and 'during_acquisition' in conditions:
        ap.error('during_acquisition requires sequential mode; use a separate predeclared parallel subset')
    reps=a.repetitions if a.repetitions is not None else {'smoke':2,'pilot':20,'confirmatory':0}[a.phase]
    if reps<=0 or a.blocks<=0:ap.error('Positive repetitions and blocks required')
    if a.phase=='smoke' and (reps>2 or a.blocks!=1):ap.error('Engineering smoke limited to two repetitions and one block')
    if a.phase=='pilot' and not re.fullmatch(r'[^\s@]+@sha256:[0-9a-fA-F]{64}',a.image):
        ap.error('Pilot requires a complete container image @sha256:<64 hex digits> digest; carry the identical digest into confirmatory')
    if a.phase in ('pilot','confirmatory'):
        if not a.smoke_report or not a.smoke_report.is_file():ap.error('Pilot/confirmatory requires --smoke-report')
        try:smoke=validate_phase_report(a.smoke_report,'smoke')
        except (ValueError,APIError,OSError) as exc:ap.error('Invalid/unsubstantiated smoke report: '+str(exc))
        if smoke.get('phase')!='smoke' or not smoke.get('engineering_acceptance_signed'):
            ap.error('Smoke report not signed/accepted')
        if not all(smoke.get(k) is True for k in ('planned_attempts_accounted_for',
            'UID_precondition_verified_on_real_server','coherence_unknown_fail_closed_verified',
            'all_expected_safe_and_negative_controls_inspected')):
            ap.error('Smoke engineering criteria not all signed as passing')
        if not smoke.get('reviewer') or not smoke.get('raw_sha256'):
            ap.error('Smoke signoff must cite reviewer and real raw-file digest')
    if a.phase=='confirmatory':
        if not a.final_lock or not a.pilot_report or not a.pilot_report.is_file():
            ap.error('Confirmatory requires --final-lock and --pilot-report')
        try:pilot=validate_phase_report(a.pilot_report,'pilot')
        except (ValueError,APIError,OSError) as exc:ap.error('Invalid/unsubstantiated pilot report: '+str(exc))
        if pilot.get('phase')!='pilot' or not pilot.get('independent_analysis_accepted'):
            ap.error('Pilot report not independently accepted')
        if pilot.get('planned_attempts_accounted_for') is not True or not all(pilot.get(k) for k in (
            'reviewer','raw_sha256','sample_size_basis_and_schedule','mean_latency_precision_target_ms')):
            ap.error('Pilot report lacks independent audit, full accounting or declared precision')
        lock=require_lock(a.final_lock,conditions,methods,a.mode,reps,a.blocks,a.image,
                          a.ttl_seconds,a.horizon_seconds,a.seed)
        if lock['pilot_report_sha256']!=sha(a.pilot_report):ap.error('Pilot report hash mismatch')
        pilot_session=json.loads((ROOT/'results'/f"{pilot['run_id']}-pilot"/'SESSION.json').read_text())
        if pilot_session.get('image')!=a.image:
            ap.error('Confirmatory image differs from pinned pilot image: must use same digest')
        if lock.get('load')!=a.load:ap.error('Locked load mismatch')
    # Preflight planning never writes a cluster resource.
    plan=frozen_planner_certificate()
    session={'phase':a.phase,'conditions':conditions,'methods':methods,'mode':a.mode,'repetitions':reps,
      'blocks':a.blocks,'total_planned_trials':len(conditions)*len(methods)*reps*a.blocks,
      'namespace':a.namespace,'run_id':a.run_id,'certificate':plan['observations'],
      'model_goals':plan['plan']['goal_count'],'source_sha256':sha(__file__),
      'model_sha256':sha(HERE/'e38_model.py'),'source_tree_sha256':source_tree_sha(),
      'image':a.image,'load':a.load,
      'ttl_seconds':a.ttl_seconds,'horizon_seconds':a.horizon_seconds,'random_seed':a.seed,
      'note':'No results are implied by this plan.'}
    if a.dry_run:print(json.dumps(session,indent=2));return 0
    if not a.ack_disposable:ap.error('Physical mutation requires --ack-disposable')
    if not a.namespace.startswith('e38-') or not a.run_id:ap.error('Invalid namespace/run ID')
    if a.output_dir.exists():ap.error('Output directory already exists: refusing to mutate resources before creating new raw log')
    verify_cluster_context(a.context)
    api=KubeAPI(a.proxy,a.namespace)
    version,status,_=api.call('GET','/version',kind='preflight')
    if status!=200:raise APIError('Cannot reach Kubernetes API via local proxy')
    # Establish a durable audit directory BEFORE the first cluster mutation.
    # Previously an image-pull, namespace or fixture failure could leave cluster
    # changes behind with no corresponding per-run setup record.
    a.output_dir.mkdir(parents=True,exist_ok=False)
    session.update(cluster_version=version,started_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                   setup_status='IN_PROGRESS')
    (a.output_dir/'SESSION.json').write_text(json.dumps(session,indent=2)+'\n')
    try:
        ensure_owned_namespace(api,a.run_id)
        ensure_fixture(api,a.image)
    except BaseException as exc:
        (a.output_dir/'SETUP_TRACE.json').write_text(json.dumps(api.requests,indent=2)+'\n')
        (a.output_dir/'SETUP_FAILED.json').write_text(json.dumps({
            'status':'SETUP_FAILED','error':repr(exc),'no_experimental_trials_executed':True,
            'setup_trace_sha256':sha(a.output_dir/'SETUP_TRACE.json')},indent=2)+'\n')
        session['setup_status']='FAILED'
        (a.output_dir/'SESSION.json').write_text(json.dumps(session,indent=2)+'\n')
        raise
    (a.output_dir/'SETUP_TRACE.json').write_text(json.dumps(api.requests,indent=2)+'\n')
    session['setup_status']='COMPLETE'
    session['setup_trace_sha256']=sha(a.output_dir/'SETUP_TRACE.json')
    (a.output_dir/'SESSION.json').write_text(json.dumps(session,indent=2)+'\n')
    results_path=a.output_dir/'RAW_TRIALS.jsonl'
    rng=random.Random(a.seed);ordinal=0;written=0;run_completed=False;interruption=None
    try:
        with results_path.open('x',encoding='utf8') as output:
            for block in range(a.blocks):
                for cond in conditions:
                    # Methods are reset to identical starting state before each trial.
                    for rep in range(reps):
                        order=methods[:];rng.shuffle(order)
                        for method in order:
                            ordinal+=1;tid=f'{a.run_id}-{ordinal:06d}'
                            trial=one_trial(api,method,cond,a.image,a.mode,a.ttl_seconds,a.horizon_seconds,
                              tid,a.phase,block,a.seed,order,rep_index=rep,load=a.load)
                            output.write(json.dumps(trial,sort_keys=True,default=str)+'\n')
                            output.flush();os.fsync(output.fileno())
                            written+=1
                            print(tid,method,cond,trial['status'],trial.get('guard_decision','NO_GATE'),flush=True)
                            # Do not silently suppress infrastructure / instrumentation failures.
                            if trial['failed'] or any('Wrong-target' in note for note in trial['safety_notes']):
                                raise APIError(f'P0 defect: {tid}; preserve logs and stop campaign')
            run_completed=True
    except BaseException as exc:
        interruption=repr(exc)
        raise
    finally:
        (a.output_dir/'COMPLETE_OR_INTERRUPTED.json').write_text(json.dumps({
          'run_id':a.run_id,'planned':session['total_planned_trials'],'written':written,
          'attempts_started':ordinal,'completed':run_completed,'interruption':interruption,
          'raw_sha256':sha(results_path) if results_path.exists() else None,
          'finished_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
          'namespace_left_intact':'Manual cleanup only after data verification.'},indent=2)+'\n')
    print('FINISHED; review RAW_TRIALS and source checksums. This is only '+a.phase+' output.')
    return 0

if __name__=='__main__':sys.exit(main())
