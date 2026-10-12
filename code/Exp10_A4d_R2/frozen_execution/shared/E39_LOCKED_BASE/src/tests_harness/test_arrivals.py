import math, statistics, time
import pytest

from harness_arrivals import (poisson_schedule, predicted_invalid_probability, ArrivalWriterProcess,
                              ArrivalWriter, ARRIVAL_TARGETS)
from harness_util import LoopbackClient
from harness_fakes import FakeKube

NS = 'e39-selftest'
REQUIRED = {'planned_offset_ms', 'actual_release_mono_ns', 't_resp_mono_ns', 'http_status',
            'resulting_resource_version', 'audit_id', 'object'}


# ------------------------------------------------------------------ schedule
def test_schedule_deterministic():
    a = poisson_schedule(424242, 17, 10.0, 2000.0)
    b = poisson_schedule(424242, 17, 10.0, 2000.0)
    assert a == b and len(a) > 0
    assert poisson_schedule(424242, 18, 10.0, 2000.0) != a
    assert poisson_schedule(424243, 17, 10.0, 2000.0) != a
    assert poisson_schedule(424242, 17, 100.0, 2000.0) != a
    # int vs float lambda gives the same schedule (canonical repr(float))
    assert poisson_schedule(424242, 17, 10, 2000.0) == a
    # a shorter window is a prefix of the longer one
    short = poisson_schedule(424242, 17, 10.0, 500.0)
    assert short == [x for x in a if x < 500.0]


def test_schedule_bounds_and_sorted():
    for lam in (1.0, 10.0, 100.0):
        s = poisson_schedule(7, 3, lam, 1000.0)
        assert s == sorted(s)
        assert all(0 <= x < 1000.0 for x in s)
    with pytest.raises(ValueError):
        poisson_schedule(1, 1, 0, 100)
    with pytest.raises(ValueError):
        poisson_schedule(1, 1, 1.0, 0)


@pytest.mark.parametrize('lam,n_trials', [(1.0, 4000), (10.0, 1500), (100.0, 400)])
def test_schedule_rate_accuracy(lam, n_trials):
    W = 1.0  # s
    counts, gaps, firsts = [], [], []
    for t in range(n_trials):
        s = poisson_schedule(20260926, t, lam, W * 1000.0)
        counts.append(len(s))
        if s:
            firsts.append(s[0])
        gaps.extend(b - a for a, b in zip(s, s[1:]))
    mean = statistics.fmean(counts)
    se = math.sqrt(lam * W / n_trials)
    print('lambda=%g: mean count %.4f (expected %.1f, SE %.4f)' % (lam, mean, lam * W, se))
    assert abs(mean - lam * W) < 4 * se
    # Poisson: variance == mean
    var = statistics.pvariance(counts)
    assert abs(var / (lam * W) - 1) < 0.15
    # P(no arrival in W) = exp(-lam W); first arrival within x ms has prob 1-exp(-lam x)
    x_ms = 1000.0 / lam * 0.5
    p_emp = sum(1 for f in firsts if f < x_ms) / n_trials
    p_th = predicted_invalid_probability(lam, x_ms / 1000.0)
    assert abs(p_emp - p_th) < 4 * math.sqrt(p_th * (1 - p_th) / n_trials)
    # inter-arrival mean on long windows (short windows bias gaps downward)
    long_gaps = []
    for t in range(20):
        s = poisson_schedule(20260926, 10_000 + t, lam, 200 * 1000.0 / lam)
        long_gaps.extend(b - a for a, b in zip(s, s[1:]))
    assert abs(statistics.fmean(long_gaps) - 1000.0 / lam) / (1000.0 / lam) < 0.05


def test_predicted_probability():
    assert predicted_invalid_probability(10, 0.005) == pytest.approx(1 - math.exp(-0.05))
    assert predicted_invalid_probability(0.01, 0.02) == pytest.approx(2e-4, rel=1e-3)


# ------------------------------------------------------------------ driver
def _allow(method, path, body):
    return method == 'GET' or (method == 'PATCH' and path.startswith(f'/api/v1/namespaces/{NS}/configmaps/'))


@pytest.fixture
def fk():
    f = FakeKube().start()
    f.seed(NS, 'response-scope', {'allowed': 'true'})
    f.seed(NS, 'evidence-a', {'value': 'true'})
    yield f
    f.stop()


@pytest.fixture
def wproc(fk):
    base = fk.base_url
    wp = ArrivalWriterProcess(lambda: LoopbackClient(base, NS, 'writer', allow=_allow))
    assert wp.ping() is None
    yield wp
    wp.stop()


def test_driver_logs_fields_and_halts(fk, wproc):
    offsets = [5.0, 20.0, 40.0, 60.0, 80.0, 120.0, 200.0, 400.0]
    aw = ArrivalWriter(wproc, 'response-scope', 'allowed', 'false', offsets, window_ms=600.0,
                       seq_key='e39-arrival-seq', lambda_per_s=100.0)
    aw.prearm()
    anchor = time.monotonic_ns()
    aw.arm(anchor)
    time.sleep(0.070)
    aw.halt_after_delete(anchor + 50_000_000, grace_ms=50.0)   # deadline = anchor + 100 ms
    res = aw.join()
    arr = res['writer_arrivals']
    meta = res['meta']
    assert [a['planned_offset_ms'] for a in arr] == [5.0, 20.0, 40.0, 60.0, 80.0]
    assert meta['released'] == 5 and meta['planned_before_halt'] == 5 and meta['planned_total'] == 8
    assert meta['halt_deadline_mono_ns'] == anchor + 100_000_000
    assert meta['lambda_per_s'] == 100.0
    commits = {c['audit_id']: c for c in fk.commits}
    rvs = []
    for i, a in enumerate(arr):
        assert REQUIRED <= set(a)
        assert a['http_status'] == 200 and a['object'] == 'response-scope' and a['new_value'] == 'false'
        assert a['intended_release_mono_ns'] == anchor + int(a['planned_offset_ms'] * 1e6)
        assert a['actual_release_mono_ns'] >= a['intended_release_mono_ns']
        assert a['actual_release_mono_ns'] - a['intended_release_mono_ns'] < 3_000_000
        assert a['t_resp_mono_ns'] > a['actual_release_mono_ns']
        assert a['audit_id'] in commits
        assert commits[a['audit_id']]['rv'] == a['resulting_resource_version']
        assert commits[a['audit_id']]['data']['e39-arrival-seq'] == str(i)
        rvs.append(int(a['resulting_resource_version']))
    assert rvs == sorted(rvs) and len(set(rvs)) == len(rvs)
    lat = sorted(a['actual_release_mono_ns'] - a['intended_release_mono_ns'] for a in arr)
    print('release lateness us:', [x // 1000 for x in lat])


def test_driver_poisson_trials_keepalive(fk, wproc):
    conns_before = fk.connections
    released = 0
    for trial in range(5):
        aw = ArrivalWriter.for_trial(wproc, 'async_scope', seed=99, trial_id=trial, lambda_per_s=100.0,
                                     window_ms=300.0)
        aw.prearm()
        anchor = time.monotonic_ns()
        aw.arm(anchor)
        time.sleep(0.05)
        aw.halt(anchor + 60_000_000)
        res = aw.join()
        planned = [o for o in poisson_schedule(99, trial, 100.0, 300.0) if o <= 60.0]
        assert [a['planned_offset_ms'] for a in res['writer_arrivals']] == planned
        released += len(planned)
    assert released > 0
    # one persistent keep-alive connection opened by the child (at start) for all trials
    assert fk.connections - conns_before <= 1
    assert ARRIVAL_TARGETS['async_scope'] == ('response-scope', 'allowed', 'false')


def test_driver_no_halt_join_stops_at_join(fk, wproc):
    aw = ArrivalWriter(wproc, 'response-scope', 'allowed', 'false', [1.0, 2.0, 5000.0], window_ms=10000.0)
    aw.prearm()
    aw.arm(time.monotonic_ns())
    time.sleep(0.03)
    t0 = time.monotonic()
    res = aw.join()           # implicit halt(now)
    assert time.monotonic() - t0 < 1.0
    assert len(res['writer_arrivals']) == 2


def test_driver_cancel_and_reuse(fk, wproc):
    aw = ArrivalWriter(wproc, 'response-scope', 'allowed', 'false', [1.0], window_ms=100.0)
    aw.prearm()
    aw.cancel()
    assert not wproc.busy
    aw2 = ArrivalWriter(wproc, 'evidence-a', 'value', 'false', [1.0], window_ms=100.0)
    aw2.prearm()
    aw2.arm(time.monotonic_ns())
    res = aw2.join()
    assert len(res['writer_arrivals']) == 1 and res['writer_arrivals'][0]['object'] == 'evidence-a'


def test_driver_error_is_logged_not_raised(fk):
    base = fk.base_url
    wp = ArrivalWriterProcess(lambda: LoopbackClient(base, NS, 'writer', allow=lambda m, p, b: m == 'GET'))
    try:
        aw = ArrivalWriter(wp, 'response-scope', 'allowed', 'false', [1.0], window_ms=100.0)
        aw.prearm()
        aw.arm(time.monotonic_ns())
        res = aw.join()
        assert 'BLOCKED' in res['writer_arrivals'][0]['error'] and res['meta']['errors'] == 1
    finally:
        wp.stop()


def test_driver_with_base_fake_api_and_kubeclient():
    try:
        from fake_api import FakeApiServer
        from e38v2_runner import KubeClient
    except Exception as exc:  # pragma: no cover
        pytest.skip('base fake_api / KubeClient not importable: %r' % exc)
    with FakeApiServer() as srv:
        base, ns = srv.base_url, srv.cluster.ns
        wp = ArrivalWriterProcess(lambda: KubeClient(base, ns, 'writer'))
        try:
            aw = ArrivalWriter.for_trial(wp, 'async_scope', seed=5, trial_id=1, lambda_per_s=100.0,
                                         window_ms=200.0)
            aw.prearm()
            anchor = time.monotonic_ns()
            aw.arm(anchor)
            time.sleep(0.25)
            aw.halt(anchor + 200_000_000)
            res = aw.join()
        finally:
            wp.stop()
        arr = res['writer_arrivals']
        assert len(arr) == len(poisson_schedule(5, 1, 100.0, 200.0)) > 0
        audit_ids = {e['auditID'] for e in srv.cluster.audit_log if e['verb'] == 'patch'}
        assert all(a['http_status'] == 200 and a['audit_id'] in audit_ids for a in arr)
        assert srv.cluster.configmaps['response-scope']['data']['allowed'] == 'false'
