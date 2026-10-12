import json, sys, time
E, RUN_ID = sys.argv[1], sys.argv[2]
sys.path.insert(0, E + '/src')
import e38v2_runner as R
T = f'{E}/.tokens_{RUN_ID}'
c = R.KubeClient(open(T + '/server.txt').read().strip(), 'e38v2-' + RUN_ID, 'reset', token_path=T + '/reset.token',
                 context=R.REQUIRED_CONTEXT, ca_path=T + '/ca.crt')
for trial in range(5):
    fx, uid = R.reset_fixture(c, webhook=False)
    t0 = time.monotonic()
    seen = []
    while time.monotonic() - t0 < 1.0:
        pod, _ = c.get_pod()
        rv = pod['metadata']['resourceVersion']
        if not seen or seen[-1][1] != rv:
            conds = [(x['type'], x.get('status'), x.get('reason')) for x in (pod.get('status') or {}).get('conditions', [])]
            seen.append((round((time.monotonic() - t0) * 1000, 2), rv, conds, (pod.get('metadata') or {}).get('managedFields') and
                         [m.get('manager') for m in pod['metadata']['managedFields']]))
        time.sleep(0.001)
    print('trial', trial)
    for s in seen:
        print('   t=%sms rv=%s conds=%s managers=%s' % s)
