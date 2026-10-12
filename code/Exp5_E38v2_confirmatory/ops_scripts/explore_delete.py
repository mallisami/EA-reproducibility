#!/usr/bin/env python3
"""Exploration (not a trial): create a Running+Ready webhook-labelled pause Pod, DELETE it (grace 0, UID
precondition) as the responder, print Audit-Id and wall-clock window. argv: E RUN_ID [n] [invalid]"""
import json, sys, time
E, RUN_ID = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 1
LABEL = (sys.argv[4] != 'nolabel') if len(sys.argv) > 4 else True
sys.path.insert(0, E + '/src')
import e38v2_runner as R
T = f'{E}/.tokens_{RUN_ID}'
srv = open(T + '/server.txt').read().strip()
ns = 'e38v2-' + RUN_ID


def mk(i):
    return R.KubeClient(srv, ns, i, token_path=f'{T}/{i}.token', context=R.REQUIRED_CONTEXT, ca_path=T + '/ca.crt')


reset, resp = mk('reset'), mk('responder')
out = []
for k in range(N):
    pod, _ = reset.get_pod()
    if pod is not None:
        reset.delete_pod(pod['metadata']['uid'])
        while reset.get_pod()[0] is not None:
            time.sleep(0.02)
    body = {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': R.POD_NAME, 'namespace': ns,
                         'labels': dict({'app': 'e38v2', 'e38v2-managed': 'true'}, **({'e38v2-webhook': 'enforce'} if LABEL else {}))},
            'spec': {'restartPolicy': 'Never', 'terminationGracePeriodSeconds': 0,
                     'automountServiceAccountToken': False, 'enableServiceLinks': False,
                     'containers': [{'name': 'target', 'image': 'registry.k8s.io/pause:3.10',
                                     'imagePullPolicy': 'Never'}]}}
    t0 = time.monotonic()
    st, h, obj = reset.request('POST', f'/api/v1/namespaces/{ns}/pods', body)
    uid = obj['metadata']['uid']
    while True:
        pod, _ = reset.get_pod()
        conds = {c['type']: c['status'] for c in (pod.get('status') or {}).get('conditions') or []}
        if pod['status'].get('phase') == 'Running' and conds.get('Ready') == 'True':
            break
        time.sleep(0.02)
    t_ready = time.monotonic() - t0
    time.sleep(0.3)
    w0 = time.time_ns()
    st, h, obj = resp.delete_pod(uid)
    w1 = time.time_ns()
    rec = {'uid': uid, 'status': st, 'audit_id': h.get('Audit-Id'), 'ready_s': round(t_ready, 3),
           'wall_send': w0, 'wall_resp': w1, 'rtt_ms': (w1 - w0) / 1e6,
           'resp_rv': ((obj or {}).get('metadata') or {}).get('resourceVersion'),
           'resp_deletionTimestamp': ((obj or {}).get('metadata') or {}).get('deletionTimestamp')}
    rec['get_after'] = reset.get_pod()[0] is None
    out.append(rec)
    print(json.dumps(rec))
