import time
import pytest

from harness_load import (LoadGenerator, LoadProcess, HostGuard, load_object_names, load_client_factory,
                          make_load_allow, ensure_objects, LOAD_NS, read_proc_stat, read_mem_available_mb)
from harness_util import HarnessAPIError
from harness_fakes import FakeKube

NAMES = load_object_names(10)


@pytest.fixture
def fk():
    f = FakeKube().start()
    ensure_objects(load_client_factory(f.base_url, NAMES)(), NAMES)
    yield f
    f.stop()


def _count(fk, t0, t1):
    return sum(1 for r in fk.requests if t0 <= r['t_mono_ns'] <= t1)


def test_allowlist_scoped_to_load_namespace(fk):
    c = load_client_factory(fk.base_url, NAMES)()
    assert c.request('GET', f'/api/v1/namespaces/{LOAD_NS}/configmaps/{NAMES[0]}')[0] == 200
    assert c.request('GET', make_load_allow(NAMES).list_path)[0] == 200
    for m, p, b in [('GET', '/api/v1/namespaces/e39-x/configmaps/response-scope', None),
                    ('PATCH', '/api/v1/namespaces/e39-x/configmaps/response-scope', {'data': {'allowed': 'false'}}),
                    ('PATCH', f'/api/v1/namespaces/{LOAD_NS}/configmaps/other', {'data': {}}),
                    ('DELETE', f'/api/v1/namespaces/{LOAD_NS}/configmaps/{NAMES[0]}', None),
                    ('GET', '/api/v1/namespaces/e39-x/pods/e39-target', None)]:
        with pytest.raises(HarnessAPIError):
            c.request(m, p, b)


def test_ensure_objects_idempotent(fk):
    assert ensure_objects(load_client_factory(fk.base_url, NAMES)(), NAMES) == 0
    assert sum(1 for k in fk.cms if k[0] == LOAD_NS) == 10


@pytest.mark.parametrize('level,target', [('moderate', 50.0), ('high', 150.0)])
def test_rate_accuracy_process(fk, level, target):
    lp = LoadProcess(guard_kwargs=None, client_factory=load_client_factory(fk.base_url, NAMES), names=NAMES,
                     level='idle', n_workers=8, seed=3, rps_window_s=3.0)
    try:
        time.sleep(0.3)
        assert lp.snapshot()['load_observed_rps'] in (None, 0.0)
        lp.set_level(level)
        time.sleep(1.0)                     # settle
        t0 = time.monotonic_ns()
        time.sleep(4.0)
        t1 = time.monotonic_ns()
        snap = lp.snapshot()
    finally:
        final = lp.stop()
    served = _count(fk, t0, t1) / ((t1 - t0) / 1e9)
    print('%s: target %.0f, server-side %.1f req/s, generator-observed %.1f req/s, dropped %d, errors %d'
          % (level, target, served, snap['load_observed_rps'], snap['dropped_ticks'], snap['errors']))
    assert abs(served - target) / target < 0.05
    assert abs(snap['load_observed_rps'] - target) / target < 0.05
    assert snap['load_level'] == level and snap['target_rps'] == target
    assert snap['errors'] == 0 and snap['host']['load_throttled'] is False
    ops = final['op_counts']
    tot = sum(ops.values())
    assert abs(ops['get'] / tot - 0.6) < 0.1 and abs(ops['list'] / tot - 0.2) < 0.08 \
        and abs(ops['patch'] / tot - 0.2) < 0.08
    # every request stayed in the load namespace
    assert all(f'/namespaces/{LOAD_NS}/' in r['path'] for r in fk.requests)


def test_idle_sends_nothing(fk):
    g = LoadGenerator(load_client_factory(fk.base_url, NAMES), NAMES, level='idle', n_workers=2).start()
    n0 = len(fk.requests)
    time.sleep(0.5)
    g.stop()
    assert len(fk.requests) == n0


# ------------------------------------------------------------------ guard (synthetic readings)
def _gen_with_guard(level='high'):
    guard = HostGuard(clock=lambda: 0.0)
    g = LoadGenerator(lambda: None, NAMES, level=level, guard=guard)
    return g, guard


def test_guard_cpu_sustained_threshold():
    g, guard = _gen_with_guard('high')
    guard.inject(cpu_pct=95.0, mem_available_mb=8000.0)
    for t in range(0, 61):                  # high for exactly 60 s: no throttle yet
        assert g.guard_step(now=float(t)) is None
    ev = g.guard_step(now=60.5)             # > 60 s
    assert ev is not None and ev['from_level'] == 'high' and ev['to_level'] == 'moderate'
    assert ev['reason'].startswith('cpu_gt_85') and ev['cpu_pct'] == 95.0
    snap = g.snapshot()
    assert snap['load_level'] == 'moderate' and snap['requested_level'] == 'high'
    assert snap['host'] == {'cpu_pct': 95.0, 'free_ram_mb': 8000.0, 'load_throttled': True}
    assert g.target_rps() == 50.0
    # still high for another > 60 s: moderate -> idle
    for t in range(61, 121):
        assert g.guard_step(now=float(t)) is None
    ev2 = g.guard_step(now=121.0)
    assert ev2['to_level'] == 'idle' and g.level == 'idle' and len(g.events) == 2
    # latched: re-requesting high stays capped
    g.set_level('high')
    assert g.level == 'idle'
    g.reset_throttle()
    assert g.level == 'high' and g.snapshot()['host']['load_throttled'] is False


def test_guard_cpu_dip_resets_timer():
    g, guard = _gen_with_guard('high')
    guard.inject(cpu_pct=90.0, mem_available_mb=8000.0)
    for t in range(0, 50):
        assert g.guard_step(now=float(t)) is None
    guard.inject(cpu_pct=80.0, mem_available_mb=8000.0)   # dip below 85 %
    assert g.guard_step(now=50.0) is None
    guard.inject(cpu_pct=90.0, mem_available_mb=8000.0)
    for t in range(51, 112):
        assert g.guard_step(now=float(t)) is None, t
    assert g.guard_step(now=111.5) is not None
    # exactly 85 % is not "> 85 %"
    g2, guard2 = _gen_with_guard('high')
    guard2.inject(cpu_pct=85.0, mem_available_mb=8000.0)
    assert all(g2.guard_step(now=float(t)) is None for t in range(0, 200))


def test_guard_low_ram_immediate():
    g, guard = _gen_with_guard('high')
    guard.inject(cpu_pct=10.0, mem_available_mb=2047.0)
    ev = g.guard_step(now=0.0)
    assert ev['reason'].startswith('mem_available_lt_2048') and ev['to_level'] == 'moderate'
    assert ev['free_ram_mb'] == 2047.0
    # persistent low RAM: one episode drops one level; the next drop only after > 60 s more
    assert all(g.guard_step(now=float(t)) is None for t in range(1, 61))
    assert g.level == 'moderate'
    assert g.guard_step(now=61.0)['to_level'] == 'idle'
    g3, guard3 = _gen_with_guard('moderate')
    guard3.inject(cpu_pct=10.0, mem_available_mb=2048.0)
    assert g3.guard_step(now=0.0) is None      # exactly 2 GB is not "< 2 GB"
    guard3.inject(cpu_pct=10.0, mem_available_mb=500.0)
    assert g3.guard_step(now=1.0)['to_level'] == 'idle'


def test_guard_logs_via_log_fn():
    lines = []
    guard = HostGuard(clock=lambda: 0.0)
    g = LoadGenerator(lambda: None, NAMES, level='high', guard=guard, log_fn=lines.append)
    guard.inject(cpu_pct=5.0, mem_available_mb=100.0)
    g.guard_step(now=0.0)
    assert lines and 'LOAD THROTTLE' in lines[0]


def test_guard_in_process_throttles_live_generator(fk):
    lp = LoadProcess(guard_kwargs={}, client_factory=load_client_factory(fk.base_url, NAMES), names=NAMES,
                     level='high', n_workers=8, seed=4, rps_window_s=2.0)
    try:
        time.sleep(2.5)
        s0 = lp.snapshot()
        assert s0['load_level'] == 'high' and s0['host']['cpu_pct'] is not None
        lp.inject(cpu_pct=None, mem_available_mb=1000.0)
        time.sleep(1.6)                     # guard samples every 1 s
        s1 = lp.snapshot()
        assert s1['load_level'] == 'moderate' and s1['host']['load_throttled'] is True
        assert s1['host']['free_ram_mb'] == 1000.0 and s1['events'][0]['to_level'] == 'moderate'
        lp.inject(None, None)
        t0 = time.monotonic_ns()
        time.sleep(3.0)
        t1 = time.monotonic_ns()
        served = _count(fk, t0, t1) / ((t1 - t0) / 1e9)
        assert abs(served - 50.0) / 50.0 < 0.08, served
    finally:
        lp.stop()


def test_real_proc_readers():
    idle, total = read_proc_stat()
    assert total > idle > 0
    assert read_mem_available_mb() > 0
    g = HostGuard()
    g.sample()
    time.sleep(0.3)
    snap, trig = g.sample()
    assert 0.0 <= snap['cpu_pct'] <= 100.0 and snap['free_ram_mb'] > 0
