#!/usr/bin/env python3
"""E38v2 RERUN probe: the target Pod is genuinely Running+Ready at gate time, and after an accepted DELETE
the container is actually terminated. Uses the runner's own code paths (reset_fixture -> wait_pod_ready,
responder DELETE with the runner's DeleteOptions) and reads container state from the node's CRI via
`docker exec e38v2-control-plane crictl` (read-only, e38v2 node only).
argv: E RUN_ID OUT_JSON [n_per_label]"""
import json, subprocess, sys, time
E, RUN_ID, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 10
sys.path.insert(0, E + '/src')
import e38v2_runner as R
T = f'{E}/.tokens_{RUN_ID}'
srv = open(T + '/server.txt').read().strip()
ns = 'e38v2-' + RUN_ID
NODE = 'e38v2-control-plane'


def mk(i):
    return R.KubeClient(srv, ns, i, token_path=f'{T}/{i}.token', context=R.REQUIRED_CONTEXT, ca_path=T + '/ca.crt')


def cri_containers(uid):
    out = subprocess.run(['docker', 'exec', NODE, 'crictl', 'ps', '-a', '-o', 'json',
                          '--label', 'io.kubernetes.pod.uid=' + uid], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    return [{'id': c['id'][:13], 'state': c['state'], 'name': c['metadata']['name']}
            for c in json.loads(out.stdout).get('containers', [])]


def cri_sandboxes(uid):
    out = subprocess.run(['docker', 'exec', NODE, 'crictl', 'pods', '-o', 'json', '--label',
                          'io.kubernetes.pod.uid=' + uid], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return None
    return [p['state'] for p in json.loads(out.stdout).get('items', [])]


reset, resp = mk('reset'), mk('responder')
res = []
for label in (True, False):
    for k in range(N):
        fx, uid = R.reset_fixture(reset, webhook=label)
        assert fx['reset_ok'], fx
        pod, _ = resp.get_pod()           # what the gate's pod read sees
        st = R.pod_state(pod)
        cri_before = cri_containers(uid)
        t0 = time.monotonic_ns()
        status, h, obj = resp.delete_pod(uid)
        t1 = time.monotonic_ns()
        md = (obj or {}).get('metadata') or {}
        gone_t = None
        for _ in range(1500):
            p, _ = resp.get_pod()
            if p is None or p['metadata']['uid'] != uid:
                gone_t = time.monotonic_ns()
                break
            time.sleep(0.02)
        cexit_t, cri_after = None, None
        for _ in range(600):
            c = cri_containers(uid)
            if c is not None and all(x['state'] != 'CONTAINER_RUNNING' for x in c):
                cexit_t, cri_after = time.monotonic_ns(), c
                break
            time.sleep(0.05)
        rec = {'webhook_label': label, 'pod_uid': uid, 'ready_wait_ms': fx['pod_ready']['wait_ms'],
               'gate_view': st, 'cri_before': cri_before, 'delete_status': status,
               'delete_audit_id': h.get('Audit-Id'), 'delete_rtt_ms': (t1 - t0) / 1e6,
               'delete_resp_rv': md.get('resourceVersion'), 'delete_resp_deletionTimestamp': md.get('deletionTimestamp'),
               'pod_gone_after_ms': None if gone_t is None else (gone_t - t0) / 1e6,
               'container_not_running_after_ms': None if cexit_t is None else (cexit_t - t0) / 1e6,
               'cri_after': cri_after}
        time.sleep(1.0)
        rec['cri_after_1s'] = cri_containers(uid)
        rec['sandboxes_after_1s'] = cri_sandboxes(uid)
        res.append(rec)
        print(json.dumps({k2: rec[k2] for k2 in ('webhook_label', 'delete_status', 'delete_rtt_ms', 'delete_resp_rv',
                                                  'pod_gone_after_ms', 'container_not_running_after_ms')}))
ok_ready = sum(1 for r in res if r['gate_view']['phase'] == 'Running' and r['gate_view']['ready'] is True)
ok_run_before = sum(1 for r in res if r['cri_before'] and any(c['state'] == 'CONTAINER_RUNNING' for c in r['cri_before']))
ok_gone = sum(1 for r in res if r['pod_gone_after_ms'] is not None)
ok_term = sum(1 for r in res if r['container_not_running_after_ms'] is not None)
summary = {'n': len(res), 'gate_view_running_and_ready': ok_ready, 'cri_container_running_before_delete': ok_run_before,
           'delete_http_200': sum(r['delete_status'] == 200 for r in res), 'pod_object_gone': ok_gone,
           'container_not_running_after_delete': ok_term,
           'delete_response_without_rv': sum(1 for r in res if r['delete_status'] == 200 and not r['delete_resp_rv']),
           'max_container_stop_ms': max([r['container_not_running_after_ms'] or 0 for r in res])}
json.dump({'summary': summary, 'trials': res}, open(OUT, 'w'), indent=1)
print('RUNNING-POD PROBE', json.dumps(summary))
print('RUNNING-POD PROBE', 'OK' if (ok_ready == ok_run_before == ok_gone == ok_term == len(res)) else 'FAILED')
