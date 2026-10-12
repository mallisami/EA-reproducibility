#!/usr/bin/env python3
"""E38v2 live verification probe (client side), using the runner's own KubeClient and identities.
Writes a JSON list of {op, identity, audit_id, rv, status, t_mono} to argv[3]."""
import json, sys, threading, time
sys.path.insert(0, sys.argv[1] + '/src')
import e38v2_runner as R

E, RUN_ID, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
T = f'{E}/.tokens_{RUN_ID}'
srv = open(T + '/server.txt').read().strip()
ns = 'e38v2-' + RUN_ID


def mk(i):
    return R.KubeClient(srv, ns, i, token_path=f'{T}/{i}.token', context=R.REQUIRED_CONTEXT, ca_path=T + '/ca.crt')


cl = {i: mk(i) for i in ('responder', 'writer', 'reset')}
who = {i: c.whoami() for i, c in cl.items()}
print('identities', who)
ops = []


def rec(op, ident, st, h, obj):
    ops.append({'op': op, 'identity': ident, 'status': st, 'audit_id': h.get('Audit-Id'),
                'rv': ((obj or {}).get('metadata') or {}).get('resourceVersion'), 't_mono': time.monotonic_ns()})


# 1) sequential writes + deletes (unlabelled pod: no webhook)
fx, uid = R.reset_fixture(cl['reset'], webhook=False)
assert fx['reset_ok'], fx
for i in range(20):
    st, h, obj = cl['writer'].patch_configmap('response-scope', {'allowed': 'true'})
    rec('patch', 'writer', st, h, obj)
    if i % 5 == 4:
        pod, _ = cl['reset'].get_pod()
        st, h, obj = cl['responder'].delete_pod(pod['metadata']['uid'])
        rec('delete', 'responder', st, h, obj)
        obj, h = cl['reset'].create_pod(webhook=False)
# 2) concurrent burst: writer and reset patch different ConfigMaps simultaneously, responder deletes
stop = time.monotonic() + 2.0
lock = threading.Lock()


def loop(ident, cm, key):
    c = mk(ident)
    while time.monotonic() < stop:
        st, h, obj = c.patch_configmap(cm, {key: 'true'})
        with lock:
            rec('patch_conc', ident, st, h, obj)
    c.close()


ths = [threading.Thread(target=loop, args=('writer', 'application-policy', 'allowed')),
       threading.Thread(target=loop, args=('reset', 'evidence-b', 'value'))]
for t in ths:
    t.start()
while time.monotonic() < stop:
    pod, _ = cl['reset'].get_pod()
    if pod is None:
        cl['reset'].create_pod(webhook=False)
        continue
    st, h, obj = cl['responder'].delete_pod(pod['metadata']['uid'])
    with lock:
        rec('delete_conc', 'responder', st, h, obj)
for t in ths:
    t.join()
R.reset_fixture(cl['reset'], webhook=False)
# 3) A3-labelled target pod, invalid state -> responder DELETE must be denied by the webhook
fx, uid = R.reset_fixture(cl['reset'], webhook=True)
st, h, obj = cl['writer'].patch_configmap('application-policy', {'allowed': 'false'})
rec('patch', 'writer', st, h, obj)
st, h, obj = cl['responder'].delete_pod(uid)
rec('delete_webhook_invalid', 'responder', st, h, obj)
print('A3 invalid DELETE ->', st, (obj or {}).get('message'))
st, h, obj = cl['writer'].patch_configmap('application-policy', {'allowed': 'true'})
rec('patch', 'writer', st, h, obj)
st, h, obj = cl['responder'].delete_pod(uid)
rec('delete_webhook_valid', 'responder', st, h, obj)
print('A3 valid DELETE ->', st)
json.dump({'identities': who, 'pod_uid_webhook': uid, 'ops': ops}, open(OUT, 'w'), indent=1)
print('ops', len(ops), 'statuses', sorted({(o['op'], o['status']) for o in ops}))
