#!/usr/bin/env python3
import argparse, json, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = 'flashir-e13'
MAN = ROOT / 'manifests'
OUT = ROOT / 'results'
LOG = ROOT / 'logs'
PROXY = None
PROXY_PORT = None
sys.path.insert(0, str(ROOT / 'scripts'))
from dcji_live_adapter import derive_live_plan, evaluate_live_commit

def run(args, check=True, stdin=None):
    p = subprocess.run(args, input=stdin, text=True, capture_output=True)
    LOG.mkdir(parents=True, exist_ok=True)
    with (LOG / 'commands.log').open('a', encoding='utf-8') as f:
        f.write('$ ' + ' '.join(args) + '\nSTDOUT:\n' + p.stdout + '\nSTDERR:\n' + p.stderr + '\n\n')
    if check and p.returncode:
        raise RuntimeError(f"command failed: {' '.join(args)} :: {p.stderr.strip()}")
    return p

def jget(kind, name):
    return json.loads(run(['kubectl', '-n', NS, 'get', kind, name, '-o', 'json']).stdout)

def apply(path):
    run(['kubectl', 'apply', '-f', str(path)])

def delete(kind, name):
    run(['kubectl', '-n', NS, 'delete', kind, name, '--ignore-not-found=true', '--wait=true'])

def wait_uid(kind, name, old=None, timeout=45):
    end = time.time() + timeout
    while time.time() < end:
        p = run(['kubectl', '-n', NS, 'get', kind, name, '-o', 'json'], check=False)
        if p.returncode == 0:
            o = json.loads(p.stdout)
            uid = o['metadata']['uid']
            if old is None or uid != old:
                return o
        time.sleep(.25)
    raise RuntimeError(f'timeout waiting for {kind}/{name} UID change')

def start_proxy():
    global PROXY, PROXY_PORT
    s = socket.socket(); s.bind(('127.0.0.1', 0)); PROXY_PORT = s.getsockname()[1]; s.close()
    PROXY = subprocess.Popen(['kubectl', 'proxy', f'--port={PROXY_PORT}', '--address=127.0.0.1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    end = time.time() + 10
    while time.time() < end:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{PROXY_PORT}/version', timeout=1).read(); return
        except Exception:
            time.sleep(.2)
    raise RuntimeError('kubectl proxy did not become ready')

def stale_uid_delete(pod, uid):
    url = f'http://127.0.0.1:{PROXY_PORT}/api/v1/namespaces/{NS}/pods/{pod}'
    body = json.dumps({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}).encode()
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='DELETE')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return {'http_status': r.status, 'blocked': False, 'body': r.read().decode()[:500]}
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors='replace')
        return {'http_status': e.code, 'blocked': e.code == 409, 'body': b[:500]}

def setup():
    run(['kubectl', 'version', '--client'])
    run(['kubectl', 'cluster-info'])
    run(['kubectl', 'create', 'namespace', NS], check=False)
    start_proxy()

def cleanup():
    global PROXY
    run(['kubectl', 'delete', 'namespace', NS, '--ignore-not-found=true', '--wait=false'], check=False)
    if PROXY:
        PROXY.terminate()
        try: PROXY.wait(timeout=5)
        except subprocess.TimeoutExpired: PROXY.kill()

def live_query(case_id, query_id):
    t0 = time.perf_counter()
    if case_id == 'F1.1' and query_id == 'query:/facts/api0_current_uid':
        obs = jget('pod', 'flashir-target')['metadata']['uid']
    elif case_id == 'F1.2' and query_id == 'query:/facts/F1_2_current_uid':
        obs = jget('pod', 'flashir-sts-0')['metadata']['uid']
    elif case_id == 'F4.1' and query_id == 'query:/facts/selector_members':
        items = json.loads(run(['kubectl', '-n', NS, 'get', 'pods', '-l', 'flashir-quarantine=true', '-o', 'json']).stdout)['items']
        obs = sorted(x['metadata']['name'] for x in items)
    elif case_id == 'F4.4' and query_id == 'query:/facts/scope_members':
        obj = jget('endpointslice', 'flashir-svc-1')
        obs = sorted(a for e in obj['endpoints'] for a in e['addresses'])
    else:
        raise RuntimeError(f'No live Kubernetes adapter for {case_id} / {query_id}')
    return obs, (time.perf_counter() - t0) * 1000.0

def gate(case_id, plan_ctx, decision_obs):
    qids = plan_ctx['plan']['critical_query_ids']
    if len(qids) != 1:
        raise RuntimeError(f'{case_id}: live v8.1.1 harness expects one frozen critical query, got {qids}')
    gate_wall_start = time.time(); gate_perf_start = time.perf_counter()
    commit_obs, query_latency_ms = live_query(case_id, qids[0])
    eval_rec = evaluate_live_commit(plan_ctx, decision_obs, commit_obs)
    gate_latency_ms = (time.perf_counter() - gate_perf_start) * 1000.0
    gate_wall_end = time.time()
    return commit_obs, query_latency_ms, gate_latency_ms, gate_wall_start, gate_wall_end, eval_rec

def timing(decision_t, transition_start_t, transition_complete_t, gate_start_t, gate_end_t, deadline_s, guard_latency_ms):
    transition_latency_s = max(0.0, transition_complete_t - transition_start_t)
    decision_to_transition_s = max(0.0, transition_complete_t - decision_t)
    decision_to_gate_s = max(0.0, gate_end_t - decision_t)
    return {
        'decision_time_unix': decision_t,
        'transition_start_time_unix': transition_start_t,
        'transition_complete_time_unix': transition_complete_t,
        'commit_gate_start_time_unix': gate_start_t,
        'commit_gate_end_time_unix': gate_end_t,
        'transition_latency_ms': transition_latency_s * 1000.0,
        'decision_to_transition_complete_ms': decision_to_transition_s * 1000.0,
        'commit_guard_latency_ms': guard_latency_ms,
        'decision_to_gate_complete_ms': decision_to_gate_s * 1000.0,
        'frozen_deadline_s': deadline_s,
        'transition_deadline_feasible': decision_to_transition_s <= deadline_s,
        'gate_completed_within_deadline': decision_to_gate_s <= deadline_s,
        'guard_overhead_ratio': (guard_latency_ms / 1000.0) / deadline_s if deadline_s > 0 else None,
        'residual_actuation_budget_s': deadline_s - decision_to_gate_s
    }

def base_record(case_id, rep, plan_ctx, decision_obs, commit_obs, query_latency_ms, gate_latency_ms, times, eval_rec):
    return {
        'case': case_id, 'rep': rep, 'execution_status': 'PASS',
        'scenario_rel': plan_ctx['scenario_rel'], 'scenario_sha256': plan_ctx['scenario_sha256'],
        'planning_runtime_ms': plan_ctx['planning_runtime_ms'],
        'critical_query_ids_derived_by_dcji': plan_ctx['plan']['critical_query_ids'],
        'critical_query_cost_units': plan_ctx['plan']['query_cost_units'],
        'decision_observation': decision_obs, 'commit_observation': commit_obs,
        'commit_query_latency_ms': query_latency_ms,
        **times, **eval_rec,
        'authorization_at_decision': True, 'authorization_at_commit': True
    }

def f11(rep):
    delete('pod', 'flashir-target'); apply(MAN / 'f1_1_pod_malicious.yaml')
    before = jget('pod', 'flashir-target'); uid0 = before['metadata']['uid']; decision_t = time.time()
    plan = derive_live_plan('F1.1')
    trans_start = time.time(); delete('pod', 'flashir-target'); apply(MAN / 'f1_1_pod_benign.yaml'); after = wait_uid('pod', 'flashir-target', uid0); trans_end = time.time()
    uid1 = after['metadata']['uid']; role1 = after['metadata']['labels'].get('flashir-role')
    commit_obs, qlat, glat, gs, ge, ev = gate('F1.1', plan, uid0)
    pre = stale_uid_delete('flashir-target', uid0); still = run(['kubectl','-n',NS,'get','pod','flashir-target'],check=False).returncode == 0
    unguarded_start = time.time(); run(['kubectl','-n',NS,'delete','pod','flashir-target','--wait=true']); unguarded_end = time.time(); gone = run(['kubectl','-n',NS,'get','pod','flashir-target'],check=False).returncode != 0
    rec = base_record('F1.1', rep, plan, uid0, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'commit_uid': uid1, 'replacement_role': role1, 'uid_changed': uid1 != uid0,
                'native_uid_precondition': pre, 'replacement_survived_stale_precondition': still,
                'unguarded_delete_effectful_on_replacement': gone, 'unguarded_action_start_time_unix': unguarded_start,
                'unguarded_action_end_time_unix': unguarded_end,
                'gate_to_unguarded_actuation_ms': max(0.0, unguarded_start-ge)*1000.0,
                'mechanism_observed': uid1 != uid0 and role1 == 'benign',
                'dcji_abort_observed': ev['dcji_live_decision'] == 'ABORT_REPLAN'})
    return rec

def f12(rep):
    delete('statefulset','flashir-sts'); delete('service','flashir-sts-headless'); apply(MAN/'f1_2_service.yaml'); apply(MAN/'f1_2_statefulset.yaml')
    before = wait_uid('pod','flashir-sts-0'); uid0 = before['metadata']['uid']; decision_t = time.time(); plan = derive_live_plan('F1.2')
    trans_start = time.time(); run(['kubectl','-n',NS,'patch','statefulset','flashir-sts','--type=merge','-p','{"spec":{"template":{"metadata":{"labels":{"flashir-role":"benign"}}}}}']); delete('pod','flashir-sts-0'); after = wait_uid('pod','flashir-sts-0',uid0); trans_end = time.time()
    uid1 = after['metadata']['uid']; role1 = after['metadata']['labels'].get('flashir-role')
    commit_obs, qlat, glat, gs, ge, ev = gate('F1.2', plan, uid0)
    pre = stale_uid_delete('flashir-sts-0',uid0); still = run(['kubectl','-n',NS,'get','pod','flashir-sts-0'],check=False).returncode == 0
    unguarded_start = time.time(); run(['kubectl','-n',NS,'delete','pod','flashir-sts-0','--wait=true']); recreated = wait_uid('pod','flashir-sts-0',uid1); unguarded_end = time.time()
    rec = base_record('F1.2', rep, plan, uid0, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'commit_uid': uid1, 'replacement_role': role1, 'uid_changed': uid1 != uid0,
                'native_uid_precondition': pre, 'replacement_survived_stale_precondition': still,
                'controller_recreated_after_unguarded_delete': recreated['metadata']['uid'] != uid1,
                'unguarded_action_start_time_unix': unguarded_start, 'unguarded_action_end_time_unix': unguarded_end,
                'gate_to_unguarded_actuation_ms': max(0.0, unguarded_start-ge)*1000.0,
                'mechanism_observed': uid1 != uid0 and role1 == 'benign',
                'dcji_abort_observed': ev['dcji_live_decision'] == 'ABORT_REPLAN'})
    return rec

def f41(rep):
    delete('pod','flashir-malicious'); delete('pod','flashir-benign'); apply(MAN/'f4_1_pods.yaml')
    before = json.loads(run(['kubectl','-n',NS,'get','pods','-l','flashir-quarantine=true','-o','json']).stdout)['items']; b = sorted(x['metadata']['name'] for x in before); decision_t = time.time(); plan = derive_live_plan('F4.1')
    trans_start = time.time(); run(['kubectl','-n',NS,'label','pod','flashir-benign','flashir-quarantine=true','--overwrite']); trans_end = time.time()
    commit_obs, qlat, glat, gs, ge, ev = gate('F4.1', plan, b)
    expanded = 'flashir-benign' not in b and 'flashir-benign' in commit_obs
    unguarded_start = time.time(); run(['kubectl','-n',NS,'delete','pods','-l','flashir-quarantine=true','--wait=true']); unguarded_end = time.time()
    rec = base_record('F4.1', rep, plan, b, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'unguarded_selector_action_includes_benign': expanded,
                'unguarded_action_start_time_unix': unguarded_start, 'unguarded_action_end_time_unix': unguarded_end,
                'gate_to_unguarded_actuation_ms': max(0.0, unguarded_start-ge)*1000.0,
                'mechanism_observed': expanded, 'dcji_abort_observed': ev['dcji_live_decision'] == 'ABORT_REPLAN'})
    return rec

def f44(rep):
    delete('endpointslice','flashir-svc-1'); delete('service','flashir-svc'); apply(MAN/'f4_4_service.yaml'); apply(MAN/'f4_4_slice_initial.yaml')
    b0 = sorted(a for e in jget('endpointslice','flashir-svc-1')['endpoints'] for a in e['addresses']); decision_t = time.time(); plan = derive_live_plan('F4.4')
    trans_start = time.time(); apply(MAN/'f4_4_slice_drift.yaml'); trans_end = time.time()
    commit_obs, qlat, glat, gs, ge, ev = gate('F4.4', plan, b0); gained = sorted(set(commit_obs)-set(b0))
    rec = base_record('F4.4', rep, plan, b0, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'gained_backend_addresses': gained, 'mechanism_observed': bool(gained),
                'dcji_abort_observed': ev['dcji_live_decision'] == 'ABORT_REPLAN', 'note':'No packet disruption induced.'})
    return rec

def _actuate_safe_control_if_execute(ev, gate_end_t):
    if ev['dcji_live_decision'] != 'EXECUTE':
        return {
            'first_actuation_start_time_unix': None,
            'actuation_end_time_unix': None,
            'gate_to_first_actuation_ms': None,
            'guarded_action_effectful': False,
            'guarded_action_attempted': False
        }
    act_start = time.time()
    run(['kubectl','-n',NS,'delete','pod','flashir-target','--wait=true'])
    act_end = time.time()
    gone = run(['kubectl','-n',NS,'get','pod','flashir-target'],check=False).returncode != 0
    return {
        'first_actuation_start_time_unix': act_start,
        'actuation_end_time_unix': act_end,
        'gate_to_first_actuation_ms': max(0.0, act_start-gate_end_t)*1000.0,
        'guarded_action_effectful': gone,
        'guarded_action_attempted': True
    }

def nc_l1(rep):
    delete('pod','flashir-target'); apply(MAN/'f1_1_pod_malicious.yaml'); before = jget('pod','flashir-target'); uid0 = before['metadata']['uid']; decision_t = time.time(); plan = derive_live_plan('F1.1')
    trans_start = trans_end = time.time()
    commit_obs, qlat, glat, gs, ge, ev = gate('F1.1', plan, uid0)
    act = _actuate_safe_control_if_execute(ev, ge)
    rec = base_record('NC-L1', rep, plan, uid0, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'control_type':'stable_state','false_abort':ev['dcji_live_decision']!='EXECUTE','relevant_state_changed':commit_obs!=uid0, **act})
    return rec

def nc_l2(rep):
    delete('pod','flashir-target'); apply(MAN/'f1_1_pod_malicious.yaml'); before = jget('pod','flashir-target'); uid0 = before['metadata']['uid']; decision_t = time.time(); plan = derive_live_plan('F1.1')
    trans_start = time.time(); run(['kubectl','-n',NS,'annotate','pod','flashir-target',f'flashir-note=irrelevant-{rep}','--overwrite']); trans_end = time.time()
    commit_obs, qlat, glat, gs, ge, ev = gate('F1.1', plan, uid0)
    act = _actuate_safe_control_if_execute(ev, ge)
    rec = base_record('NC-L2', rep, plan, uid0, commit_obs, qlat, glat, timing(decision_t, trans_start, trans_end, gs, ge, plan['deadline_s'], glat), ev)
    rec.update({'control_type':'irrelevant_mutation','false_abort':ev['dcji_live_decision']!='EXECUTE','relevant_state_changed':commit_obs!=uid0, **act})
    return rec

def f21():
    items = json.loads(run(['kubectl','get','nodes','-o','json']).stdout)['items']
    ready = [n['metadata']['name'] for n in items if {c['type']:c['status'] for c in n.get('status',{}).get('conditions',[])}.get('Ready') == 'True']
    if not ready: return {'case':'F2.1','scientific_status':'N/A','reason':'no Ready node'}
    seen = {}
    for i in range(50):
        name=f'flashir-ip-{i}'
        manifest='apiVersion: v1\nkind: Pod\nmetadata:\n  name: '+name+'\n  namespace: '+NS+'\nspec:\n  containers:\n  - name: hold\n    image: registry.k8s.io/pause:3.10.1\n'
        run(['kubectl','apply','-f','-'],stdin=manifest); end=time.time()+60; obj=None; ip=None
        while time.time()<end:
            obj=jget('pod',name); ip=obj.get('status',{}).get('podIP')
            if ip: break
            time.sleep(.5)
        if not ip: delete('pod',name); return {'case':'F2.1','scientific_status':'N/A','reason':'Pod IP unavailable'}
        uid=obj['metadata']['uid']
        if ip in seen and seen[ip]!=uid: delete('pod',name); return {'case':'F2.1','scientific_status':'REUSED','reused_ip':ip,'old_uid':seen[ip],'new_uid':uid,'churn_attempt':i+1}
        seen[ip]=uid; delete('pod',name)
    return {'case':'F2.1','scientific_status':'NOT_REPRODUCIBLE','attempts':50,'unique_ips':len(seen)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--attempt',type=int,choices=[1,2],required=True); a=ap.parse_args()
    OUT.mkdir(exist_ok=True); path=OUT/f'e13_live_attempt_{a.attempt}.json'
    if path.exists(): raise SystemExit('attempt file exists; refusing overwrite')
    rec={'attempt':a.attempt,'started_at_unix':time.time(),'status':None,'protocol_freeze_id':'FLASHIR-E13-LIVE-FREEZE-20260906-V3.1','positive_results':[],'control_results':[],'conditional_result':None}
    try:
        setup()
    except Exception as e:
        rec['status']='INFRA_FAILURE'; rec['error']=str(e); path.write_text(json.dumps(rec,indent=2)+'\n'); print(json.dumps(rec,indent=2)); return 2
    try:
        for rep in range(1,21):
            for name,fn in [('F1.1',f11),('F1.2',f12),('F4.1',f41),('F4.4',f44)]:
                try: rec['positive_results'].append(fn(rep))
                except Exception as e: rec['positive_results'].append({'case':name,'rep':rep,'execution_status':'CASE_FAILURE','error':str(e)})
            for name,fn in [('NC-L1',nc_l1),('NC-L2',nc_l2)]:
                try: rec['control_results'].append(fn(rep))
                except Exception as e: rec['control_results'].append({'case':name,'rep':rep,'execution_status':'CASE_FAILURE','error':str(e)})
        try: rec['conditional_result']=f21()
        except Exception as e: rec['conditional_result']={'case':'F2.1','scientific_status':'CASE_FAILURE','error':str(e)}
        all_rows = rec['positive_results'] + rec['control_results']
        rec['status']='PASS' if len(rec['positive_results'])==80 and len(rec['control_results'])==40 and all(x.get('execution_status')=='PASS' for x in all_rows) else 'CASE_FAILURE'
        rec['completed_at_unix']=time.time()
    finally:
        cleanup()
    path.write_text(json.dumps(rec,indent=2)+'\n')
    summary={
        'attempt':a.attempt,'status':rec['status'],
        'positive_trials_recorded':sum(x.get('execution_status')=='PASS' for x in rec['positive_results']),
        'positive_trials_scheduled':80,
        'control_trials_recorded':sum(x.get('execution_status')=='PASS' for x in rec['control_results']),
        'control_trials_scheduled':40,
        'conditional':rec['conditional_result']
    }
    print(json.dumps(summary,indent=2)); return 0 if rec['status']=='PASS' else 3

if __name__=='__main__':
    sys.exit(main())
