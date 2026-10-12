import http.client, json, os, statistics, time
import pytest

from harness_delay_proxy import (DelayProxy, DelayProxyProcess, calibrate, is_configmap_get,
                                 PairedAcquisitionDriver, guard_orders, make_dcji_planner, FLAGS)
from harness_util import LoopbackClient
from harness_fakes import FakeKube, make_cert

NS = 'e39-ctest'
CM_PATH = f'/api/v1/namespaces/{NS}/configmaps/evidence-a'
ALLOW_ALL = lambda m, p, b: True
CALIB_OUT = os.environ.get('E39_CALIB_OUT', '/tmp/e39_proxy_calibration.json')


@pytest.fixture
def fk():
    f = FakeKube().start()
    for k in FLAGS:
        f.seed(NS, 'evidence-' + k, {'value': 'true'})
    yield f
    f.stop()


def _client(base, **kw):
    return LoopbackClient(base, NS, 'responder', allow=ALLOW_ALL, **kw)


def _t(client, method, path, body=None):
    t0 = time.monotonic_ns()
    r = client.request(method, path, body)
    return (time.monotonic_ns() - t0) / 1e6, r


def test_classifier():
    assert is_configmap_get('GET', CM_PATH)
    assert is_configmap_get('GET', CM_PATH + '?resourceVersion=0')
    assert not is_configmap_get('GET', CM_PATH + '?watch=true')
    assert not is_configmap_get('GET', f'/api/v1/namespaces/{NS}/configmaps')
    assert not is_configmap_get('GET', f'/api/v1/namespaces/{NS}/configmaps?labelSelector=x')
    assert not is_configmap_get('GET', f'/api/v1/namespaces/{NS}/pods/p')
    assert not is_configmap_get('PATCH', CM_PATH)


def test_loopback_only():
    with pytest.raises(ValueError):
        DelayProxy('http://10.0.0.1:6443')
    with pytest.raises(ValueError):
        DelayProxy('https://127.0.0.1:6443')      # https upstream requires pinned CA
    with pytest.raises(ValueError):
        DelayProxy('http://localhost:6443')


def test_delay_applied_to_cm_get_only_and_passthrough(fk):
    with DelayProxy(fk.base_url, delay_ms=100.0) as px:
        c = _client(px.base_url)
        d = _client(fk.base_url)
        _t(c, 'GET', CM_PATH)
        lat_get, (st, h, obj) = _t(c, 'GET', CM_PATH)
        assert st == 200 and obj['data']['value'] == 'true'
        assert 99.0 <= lat_get < 115.0
        # non-GET and non-ConfigMap-GET: not delayed, bodies/responses unchanged
        lat_patch, (st, h, obj) = _t(c, 'PATCH', CM_PATH, {'data': {'value': 'false'}})
        assert st == 200 and obj['data']['value'] == 'false' and lat_patch < 20
        fk_patch = [r for r in fk.requests if r['method'] == 'PATCH'][-1]
        assert json.loads(fk_patch['body']) == {'data': {'value': 'false'}}
        assert fk_patch['headers']['Content-Type'] == 'application/merge-patch+json'
        body = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'x1'}, 'data': {'k': 'v'}}
        lat_post, (st, _, obj) = _t(c, 'POST', f'/api/v1/namespaces/{NS}/configmaps', body)
        assert st == 201 and lat_post < 20
        lat_list, (st, _, obj) = _t(c, 'GET', f'/api/v1/namespaces/{NS}/configmaps')
        assert st == 200 and obj['kind'] == 'ConfigMapList' and lat_list < 20
        lat_del, (st, _, _) = _t(c, 'DELETE', f'/api/v1/namespaces/{NS}/configmaps/x1')
        assert st == 200 and lat_del < 20
        lat_404, (st, _, _) = _t(c, 'GET', f'/api/v1/namespaces/{NS}/pods/p')
        assert st == 404 and lat_404 < 20
        # same responses as direct access
        assert d.get_configmap('evidence-a')[2] == c.get_configmap('evidence-a')[2]
        log = px.take_log()
        delayed = [r for r in log if r['delayed']]
        assert all(r['method'] == 'GET' and '/configmaps/evidence-a' in r['path'] for r in delayed)
        assert {r['method'] for r in log if not r['delayed']} >= {'PATCH', 'POST', 'DELETE', 'GET'}


def test_headers_and_audit_id_preserved(fk):
    with DelayProxy(fk.base_url, delay_ms=5.0) as px:
        c = _client(px.base_url, token='test-token-not-secret')
        for path in (CM_PATH, f'/api/v1/namespaces/{NS}/configmaps'):
            st, h, _ = c.request('GET', path)
            up = fk.requests[-1]
            assert h.get('Audit-Id') == up['audit_id']
            assert up['headers']['Authorization'] == 'Bearer test-token-not-secret'
            assert up['headers']['User-Agent'] == 'e39-harness/responder'
            assert up['headers']['Accept'] == 'application/json'
        st, h, _ = c.request('PATCH', CM_PATH, {'data': {'value': 'true'}})
        assert h.get('Audit-Id') == fk.commits[-1]['audit_id']
        log = px.take_log()
        assert log[-1]['audit_id'] == fk.commits[-1]['audit_id']


def test_keepalive_both_sides(fk):
    with DelayProxy(fk.base_url, delay_ms=1.0) as px:
        c = _client(px.base_url)
        conns0 = fk.connections
        for _ in range(30):
            assert c.get_configmap('evidence-b')[0] == 200
            assert c.request('PATCH', CM_PATH, {'data': {'value': 'true'}})[0] == 200
        assert c.connections_opened == 1
        assert fk.connections - conns0 == 1
        assert px.upstream_connections_opened == 1
        assert len({r['conn_id'] for r in fk.requests[-60:]}) == 1


def test_chunked_stream_passthrough(fk):
    with DelayProxy(fk.base_url, delay_ms=50.0) as px:
        conn = http.client.HTTPConnection('127.0.0.1', px.port, timeout=10)
        conn.request('GET', '/stream-test')
        r = conn.getresponse()
        data = r.read()
        assert r.status == 200 and data.count(b'"event"') == 3
        assert r.getheader('Audit-Id') == fk.requests[-1]['audit_id']
        conn.close()


def test_tls_listener_and_pinned_upstream(tmp_path):
    pair = make_cert(str(tmp_path))
    if pair is None:
        pytest.skip('openssl not available')
    cert, key = pair
    other_dir = tmp_path / 'other'
    other_dir.mkdir()
    other_cert, _ = make_cert(str(other_dir))
    with FakeKube(tls_cert=cert, tls_key=key) as up:
        up.seed(NS, 'evidence-a', {'value': 'true'})
        with DelayProxy(up.base_url, delay_ms=20.0, upstream_ca=cert, tls_cert=cert, tls_key=key) as px:
            assert px.base_url.startswith('https://')
            c = _client(px.base_url, ca_path=cert, token='tkn')
            lat, (st, h, obj) = _t(c, 'GET', CM_PATH)
            assert st == 200 and obj['data']['value'] == 'true' and lat >= 20.0
            assert h.get('Audit-Id') == up.requests[-1]['audit_id']
            assert up.requests[-1]['headers']['Authorization'] == 'Bearer tkn'
        # wrong pinned CA -> upstream verification fails -> 502, request never reaches upstream
        n0 = len(up.requests)
        with DelayProxy(up.base_url, delay_ms=0.0, upstream_ca=other_cert) as px2:
            st, _, _ = _client(px2.base_url).request('GET', CM_PATH)
            assert st == 502
        assert len(up.requests) == n0


def test_calibration_process_proxy(fk):
    """Measured added delay vs requested (separate proxy process, as used live)."""
    with DelayProxyProcess(fk.base_url) as px:
        pc = _client(px.base_url)
        dc = _client(fk.base_url)
        res = calibrate(px, pc, dc, CM_PATH, delays_ms=(0.0, 50.0, 200.0, 1000.0), n=20, warmup=5)
    with open(CALIB_OUT, 'w') as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res, indent=1))
    for row in res['per_delay']:
        assert abs(row['err_mean_ms']) < 1.5, row
        assert row['err_p95_abs_ms'] < 3.0, row
    assert res['proxy_overhead_ms'] < 5.0


@pytest.mark.parametrize('position', ['after', 'split'])
def test_delay_positions(fk, position):
    with DelayProxy(fk.base_url, delay_ms=60.0, delay_position=position) as px:
        c = _client(px.base_url)
        c.get_configmap('evidence-a')
        lats = [_t(c, 'GET', CM_PATH)[0] for _ in range(5)]
        assert 59.5 <= statistics.median(lats) < 70
        rec = px.take_log()[-1]
        assert rec['before_ns'] + rec['after_ns'] == 60_000_000
        assert (rec['before_ns'] == 0) == (position == 'after')


# ------------------------------------------------------------------ paired acquisition
def test_guard_orders_balance_and_determinism():
    o = guard_orders(123, 60)
    assert len(o) == 60 and o.count('dcji_first') == 30 and o.count('fullrefresh_first') == 30
    for i in range(0, 60, 4):
        assert o[i:i + 4].count('dcji_first') == 2
    assert guard_orders(123, 60) == o
    assert guard_orders(124, 60) != o
    odd = guard_orders(5, 61)
    assert abs(odd.count('dcji_first') - odd.count('fullrefresh_first')) <= 1
    # not a fixed alternation: across many seeds each position is ~50/50
    firsts = [guard_orders(s, 4)[0] for s in range(400)]
    assert 160 < firsts.count('dcji_first') < 240


def _stub_planner():
    time.sleep(0.002)
    return ('a', 'c', 'e')


@pytest.mark.parametrize('acq', ['sequential', 'parallel'])
@pytest.mark.parametrize('planning', ['precomputed', 'per_incident'])
def test_paired_counts_and_fields(fk, acq, planning):
    with DelayProxy(fk.base_url, delay_ms=20.0) as px:
        base = px.base_url
        hooks = []
        drv = PairedAcquisitionDriver(lambda: _client(base), _stub_planner, acq, planning, 20.0, seed=7,
                                      n_pairs=8, before_guard=lambda arm, pid, pos: hooks.append((arm, pid, pos)))
        try:
            recs = drv.run_all()
        finally:
            drv.close()
    assert len(recs) == 16 and len(hooks) == 16
    for pid in range(8):
        pair = [r for r in recs if r['pair_id'] == pid]
        assert {r['arm'] for r in pair} == {'A1_dcji', 'A2_fullrefresh'}
        first = pair[0]['arm']
        assert pair[0]['guard_order'] == ('dcji_first' if first == 'A1_dcji' else 'fullrefresh_first')
    for r in recs:
        for k in ('experiment', 'arm', 'injected_latency_ms', 'acquisition_mode', 'planning_mode',
                  'planning_ms', 'guard_order', 'pair_id'):
            assert k in r
        assert r['experiment'] == 'C' and r['acquisition_mode'] == acq and r['planning_mode'] == planning
        a = r['acquisition']
        assert a['all_reads_ok'] and a['justified']
        if r['arm'] == 'A1_dcji':
            assert a['n_reads'] == 3 and a['flags_read'] == ['a', 'c', 'e']
            assert (r['planning_ms'] is not None and r['planning_ms'] >= 2.0) == (planning == 'per_incident')
        else:
            assert a['n_reads'] == 6 and r['planning_ms'] is None
        assert all(x['audit_id'] for x in a['reads'])
        if acq == 'sequential':
            assert a['refresh_ms'] >= 20.0 * a['n_reads']
            ts = a['reads']
            assert all(ts[i]['t_recv_mono_ns'] <= ts[i + 1]['t_send_mono_ns'] for i in range(len(ts) - 1))
        else:
            assert 20.0 <= a['refresh_ms'] < 20.0 * 2.5
    n_get = sum(1 for q in fk.requests if q['method'] == 'GET')
    assert n_get == 8 * (3 + 6)


def test_paired_detects_cut_evidence(fk):
    """ea_attack-like fixture via hook: b,d,f cut -> DCJI (reads a,c,e only) still justified,
    FullRefresh not (out-of-model mutation, E39-G semantics)."""
    def hook(arm, pid, pos):
        for k in 'bdf':
            fk.seed(NS, 'evidence-' + k, {'value': 'false'})
    drv = PairedAcquisitionDriver(lambda: _client(fk.base_url), _stub_planner, 'sequential', 'precomputed', 0.0,
                                  seed=1, n_pairs=2, before_guard=hook)
    try:
        recs = drv.run_all()
    finally:
        drv.close()
    for r in recs:
        assert r['acquisition']['justified'] == (r['arm'] == 'A1_dcji')


F5_CANDIDATES = [os.environ.get('E39_F5_PATH', ''),
                 os.path.expanduser('~/e39_20260926/frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json'),
                 os.path.expanduser('~/e38v2r_20260926/frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json')]


def test_real_planner_certificate():
    f5 = next((p for p in F5_CANDIDATES if p and os.path.exists(p)), None)
    if f5 is None:
        pytest.skip('E37 F5 scenario not found')
    plan = make_dcji_planner(f5, horizon_s=30.0)
    t0 = time.monotonic()
    assert plan() == ('a', 'c', 'e')
    print('planner time per incident: %.1f ms' % ((time.monotonic() - t0) * 1000))
