#!/usr/bin/env python3
"""RV order vs audit timestamp order with value-CHANGING writes (no-op PATCHes do not commit).
Phase A: sequential toggles + pod deletes. Phase B: two concurrent writers + concurrent deletes.
Then reads audit on stdin? No: writes ops JSON; audit comparison done by probe_order_audit (same file, --audit)."""
import json, sys, threading, time
E = sys.argv[1]; RUN_ID = sys.argv[2]; OUT = sys.argv[3]
sys.path.insert(0, E + '/src')
import e38v2_runner as R
T = f'{E}/.tokens_{RUN_ID}'
srv = open(T + '/server.txt').read().strip()
ns = 'e38v2-' + RUN_ID


def mk(i):
    return R.KubeClient(srv, ns, i, token_path=f'{T}/{i}.token', context=R.REQUIRED_CONTEXT, ca_path=T + '/ca.crt')


ops, lock = [], threading.Lock()


def rec(op, st, h, obj):
    with lock:
        ops.append({'op': op, 'status': st, 'audit_id': h.get('Audit-Id'),
                    'rv': ((obj or {}).get('metadata') or {}).get('resourceVersion')})


reset, writer, resp = mk('reset'), mk('writer'), mk('responder')
R.reset_fixture(reset)
for i in range(60):
    st, h, obj = writer.patch_configmap('response-scope', {'allowed': 'false' if i % 2 == 0 else 'true'})
    rec('seq_patch', st, h, obj)
    if i % 6 == 5:
        pod, _ = reset.get_pod()
        st, h, obj = resp.delete_pod(pod['metadata']['uid'])
        rec('seq_delete', st, h, obj)
        reset.create_pod()
stop = time.monotonic() + 3.0


def loop(ident, cm, key):
    c, i = mk(ident), 0
    while time.monotonic() < stop:
        st, h, obj = c.patch_configmap(cm, {key: 'false' if i % 2 == 0 else 'true'})
        rec('conc_patch_' + ident, st, h, obj)
        i += 1
    c.close()


ths = [threading.Thread(target=loop, args=('writer', 'application-policy', 'allowed')),
       threading.Thread(target=loop, args=('reset', 'evidence-b', 'value'))]
for t in ths:
    t.start()
r2 = mk('reset')
while time.monotonic() < stop:
    pod, _ = r2.get_pod()
    if pod is None:
        r2.create_pod()
        continue
    st, h, obj = resp.delete_pod(pod['metadata']['uid'])
    rec('conc_delete', st, h, obj)
for t in ths:
    t.join()
R.reset_fixture(reset)
json.dump(ops, open(OUT, 'w'))
print('ops', len(ops))
