"""E39 A4m: fake list/watch, mirror controller (thread + separate process), mirror lag logging."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import arms_commit as AC  # noqa: E402
import e39_kube as K  # noqa: E402
from e39_fragments import validate_fragment  # noqa: E402
from fake_api import FakeApiServer  # noqa: E402
from mirror_controller import JsonlSink, MirrorController  # noqa: E402

NS = 'e40-selftest'


@pytest.fixture
def api():
    srv = FakeApiServer(namespace=NS).start()
    yield srv
    srv.stop()


@pytest.fixture
def clients(api):
    cs = {i: K.E39Client(api.base_url, NS, i) for i in ('responder', 'writer', 'reset')}
    yield cs
    for c in cs.values():
        c.close()


def mk_mirror(api, resync_s=30.0, watch_timeout_s=30):
    pc = K.E39Client(api.base_url, NS, 'mirror')
    wc = K.E39Client(api.base_url, NS, 'mirror')
    return MirrorController(pc, wc, JsonlSink(), resync_s=resync_s, watch_timeout_s=watch_timeout_s)


def a4m_pod(reset):
    """Reset identity replaces the Pod with annotations = current ConfigMap values (fixture)."""
    pod, _ = reset.get_pod()
    if pod is not None:
        assert reset.delete_pod(pod['metadata']['uid'])[0] == 200
    cms = {n: reset.get_configmap(n)[2] for n in K.POLICY_CMS}
    st, _, obj = reset.create_target_pod(annotations=AC.annotations_from_configmaps(cms))
    assert st == 201
    return obj['metadata']['uid']


def wait_until(pred, timeout=5.0, step=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def updates(mc, **kw):
    return [r for r in mc.sink.records if r['kind'] == 'update'
            and all(r.get(k) == v for k, v in kw.items())]


# ------------------------------------------------------------------ fake list/watch
def read_events(resp, n):
    out = []
    while len(out) < n:
        line = resp.readline()
        if not line:
            break
        if line.strip():
            out.append(json.loads(line))
    return out


def test_fake_list_and_watch(api, clients):
    w = clients['writer']
    st, _, lst = w.list_configmaps()
    assert st == 200 and lst['kind'] == 'ConfigMapList' and len(lst['items']) == len(K.ALL_CMS)
    rv = lst['metadata']['resourceVersion']
    conn, resp = w.open_watch(f'/api/v1/namespaces/{NS}/configmaps', resource_version=rv, timeout_seconds=2)
    assert resp.status == 200
    st, _, obj = w.patch_configmap('evidence-b', {'value': 'false'})
    ev = read_events(resp, 1)[0]
    assert ev['type'] == 'MODIFIED' and ev['object']['metadata']['name'] == 'evidence-b'
    assert ev['object']['metadata']['resourceVersion'] == obj['metadata']['resourceVersion']
    assert ev['object']['data']['value'] == 'false'
    conn.close()


def test_fake_watch_without_rv_sends_added(api, clients):
    conn, resp = clients['writer'].open_watch(f'/api/v1/namespaces/{NS}/configmaps', timeout_seconds=1)
    evs = read_events(resp, len(K.ALL_CMS))
    assert [e['type'] for e in evs] == ['ADDED'] * len(K.ALL_CMS)
    conn.close()


def test_fake_watch_timeout_and_410(api, clients):
    w = clients['writer']
    rv = w.list_configmaps()[2]['metadata']['resourceVersion']
    t0 = time.monotonic()
    conn, resp = w.open_watch(f'/api/v1/namespaces/{NS}/configmaps', resource_version=rv, timeout_seconds=1)
    assert resp.readline() == b'' and 0.8 < time.monotonic() - t0 < 5
    conn.close()
    w.patch_configmap('evidence-a', {'value': 'true'})
    api.cluster.compact()
    conn, resp = w.open_watch(f'/api/v1/namespaces/{NS}/configmaps', resource_version=rv, timeout_seconds=1)
    ev = read_events(resp, 1)[0]
    assert ev['type'] == 'ERROR' and ev['object']['code'] == 410
    conn.close()


# ------------------------------------------------------------------ mirror controller
def test_mirror_propagates_and_logs_lag(api, clients):
    uid = a4m_pod(clients['reset'])
    mc = mk_mirror(api).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)
        time.sleep(0.05)
        st, h, obj = clients['writer'].patch_configmap('response-scope', {'allowed': 'false'})
        src_rv, src_wall = obj['metadata']['resourceVersion'], time.time_ns()
        pod = wait_until(lambda: (lambda p: p if p['metadata']['annotations'].get('e38.scope') == 'false'
                                  else None)(clients['reset'].get_pod()[0]))
        assert pod and pod['metadata']['uid'] == uid
        e = wait_until(lambda: AC.find_mirror_entry(mc.sink.records, 'response-scope', src_rv))
        assert e['via'] == 'watch' and e['event_type'] == 'MODIFIED' and e['http_status'] == 200
        assert e['source_commit_rv'] == src_rv and int(e['mirror_commit_rv']) > int(src_rv)
        assert e['annotation_key'] == 'e38.scope' and e['pod_uid'] == uid
        validate_fragment('mirror_log_update', e)
        # mirror PATCH is audited under the mirror identity with the logged Audit-ID
        ev = [a for a in api.cluster.audit_log if a['auditID'] == e['mirror_audit_id']]
        assert len(ev) == 1 and ev[0]['user']['username'].endswith('e40-mirror')
        assert ev[0]['objectRef']['resource'] == 'pods' and ev[0]['verb'] == 'patch'
        frag = AC.mirror_fragment('response-scope', src_rv, src_wall, e)
        validate_fragment('mirror', frag)
        lag = AC.mirror_lag(frag)
        assert lag['rv_gap'] >= 1 and lag['wall_lag_ms'] is not None
        assert AC.mirror_converged(clients['reset'])[0]
    finally:
        mc.stop()
    assert any(r['kind'] == 'stop' for r in mc.sink.records)


def test_mirror_unpropagated_fragment_has_null_mirror_fields():
    frag = AC.mirror_fragment('evidence-a', '123', 1, None)
    validate_fragment('mirror', frag)
    assert AC.mirror_lag(frag) == {'rv_gap': None, 'wall_lag_ms': None}


def test_mirror_skips_unchanged_values(api, clients):
    a4m_pod(clients['reset'])
    rv0 = clients['reset'].get_pod()[0]['metadata']['resourceVersion']
    mc = mk_mirror(api).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)
        clients['writer'].patch_configmap('evidence-c', {'value': 'true'})   # same value
        assert wait_until(lambda: updates(mc, source_object='evidence-c', via='watch'))
        assert updates(mc, source_object='evidence-c', via='watch')[0]['skipped'] is True
        assert clients['reset'].get_pod()[0]['metadata']['resourceVersion'] == rv0
    finally:
        mc.stop()


def test_mirror_resync_fallback_when_watch_stalls(api, clients):
    a4m_pod(clients['reset'])
    mc = mk_mirror(api, resync_s=0.3).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)
        api.cluster.set_watch_paused(True)             # watch delivers nothing
        clients['writer'].patch_configmap('evidence-d', {'value': 'false'})
        assert wait_until(lambda: clients['reset'].get_pod()[0]['metadata']['annotations']
                          .get('e38.evidence/d') == 'false', timeout=5)
        ups = [u for u in updates(mc, source_object='evidence-d') if not u['skipped']]
        assert ups and ups[0]['via'] == 'resync' and ups[0]['event_type'] == 'LIST'
        assert any(r['kind'] == 'resync' and r['reason'] == 'periodic' for r in mc.sink.records)
    finally:
        api.cluster.set_watch_paused(False)
        mc.stop()


def test_mirror_rewatch_and_relist_after_410(api, clients):
    a4m_pod(clients['reset'])
    mc = mk_mirror(api).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)
        # watch closed by the server -> re-watch from last RV, no relist needed
        api.cluster.close_watches()
        assert wait_until(lambda: mc.stats['watch_starts'] >= 2)
        clients['writer'].patch_configmap('evidence-e', {'value': 'false'})
        assert wait_until(lambda: clients['reset'].get_pod()[0]['metadata']['annotations']
                          .get('e38.evidence/e') == 'false')
        # history compacted (the mirror's own Pod PATCH advanced the global revision past its last
        # ConfigMap RV) + stream closed -> re-watch gets 410 Expired -> relist (resync reason 'relist')
        api.cluster.compact()
        api.cluster.close_watches()
        assert wait_until(lambda: any(r['kind'] == 'resync' and r['reason'] == 'relist'
                                      for r in mc.sink.records), timeout=5)
        assert any(r['kind'] == 'error' and r.get('code') == 410 for r in mc.sink.records)
        clients['writer'].patch_configmap('evidence-e', {'value': 'true'})
        assert wait_until(lambda: clients['reset'].get_pod()[0]['metadata']['annotations']
                          .get('e38.evidence/e') == 'true')
    finally:
        mc.stop()


# ------------------------------------------------------------------ A4m end to end
def test_a4m_window_is_mirror_lag(api, clients):
    """Write after the gate that the mirror has NOT yet propagated -> DELETE succeeds (the invalid
    action commits: the window moved to the mirror lag). Once propagated -> 409."""
    uid = a4m_pod(clients['reset'])
    mc = mk_mirror(api).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)
        assert AC.wait_mirror_converged(clients['reset'], timeout_s=2)['converged']
        api.cluster.set_watch_paused(True)                 # simulated mirror lag
        res = AC.run_commit_bound(
            clients['responder'], AC.ARM_MIRROR, uid,
            after_gate=lambda g: clients['writer'].patch_configmap('response-scope', {'allowed': 'false'}))
        assert res['outcome'] == 'EXECUTED' and res['delete']['http_status'] == 200
    finally:
        api.cluster.set_watch_paused(False)
        mc.stop()

    # propagated before the DELETE -> 409
    clients['reset'].patch_configmap('response-scope', {'allowed': 'true'})
    uid = a4m_pod(clients['reset'])
    mc = mk_mirror(api).start()
    try:
        assert wait_until(lambda: mc.stats['watch_starts'] >= 1)

        def write_and_wait(g):
            st, _, obj = clients['writer'].patch_configmap('response-scope', {'allowed': 'false'})
            assert wait_until(lambda: AC.find_mirror_entry(mc.sink.records, 'response-scope',
                                                           obj['metadata']['resourceVersion']))

        res = AC.run_commit_bound(clients['responder'], AC.ARM_MIRROR, uid, after_gate=write_and_wait)
        assert res['outcome'] == 'CONFLICT_409' and res['conflict_kind'] == 'resource_version'
    finally:
        mc.stop()


def test_mirror_separate_process(api, clients, tmp_path):
    a4m_pod(clients['reset'])
    log = tmp_path / 'mirror.jsonl'
    p = subprocess.Popen([sys.executable, os.path.join(HERE, 'mirror_controller.py'), '--api', api.base_url,
                          '--namespace', NS, '--identity', 'mirror', '--log', str(log), '--resync-s', '1',
                          '--duration-s', '30'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert p.stdout.readline().strip() == b'MIRROR_READY'
        assert wait_until(lambda: log.exists() and b'watch_start' in log.read_bytes(), timeout=5)
        st, _, obj = clients['writer'].patch_configmap('application-policy', {'allowed': 'false'})
        assert wait_until(lambda: clients['reset'].get_pod()[0]['metadata']['annotations']
                          .get('e38.app-policy') == 'false', timeout=5)
    finally:
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
    assert p.returncode == 0
    recs = [json.loads(x) for x in log.read_text().splitlines()]
    e = AC.find_mirror_entry(recs, 'application-policy', obj['metadata']['resourceVersion'])
    assert e and e['identity'] == 'mirror' and e['mirror_audit_id']
    assert recs[-1]['kind'] == 'stop'
    ev = [a for a in api.cluster.audit_log if a['auditID'] == e['mirror_audit_id']]
    assert ev and ev[0]['user']['username'].endswith('e40-mirror')
