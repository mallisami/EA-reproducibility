#!/usr/bin/env python3
"""E39-D: background API load generator + host guard.

LoadGenerator issues a GET / LIST / PATCH mix against its OWN ConfigMaps in namespace
e40-load, as a separate identity, at a target request rate (open-loop pacing: one
dispatcher emits ticks every 1/rps; a pool of workers, each with its own keep-alive
connection, executes them; ticks are dropped (counted) instead of bursting when the
workers fall behind). Levels: idle 0, moderate 50, high 150 req/s.

HostGuard samples /proc/stat (aggregate CPU busy %) and /proc/meminfo (MemAvailable)
every second. Throttle rule (spec E39-D SAFETY): CPU > 85 % continuously for > 60 s,
or MemAvailable < 2 GB -> drop one level (high -> moderate -> idle); the cap is
LATCHED (load_throttled = True) for the rest of the session unless reset_throttle()
is called, and each event is logged. Low memory is edge-triggered (one drop on entering
the state; another only if it persists > 60 s more). Test hook: HostGuard.inject(cpu_pct=, mem_available_mb=)
overrides the /proc readings; HostGuard.sample(now=) can be driven with a fake clock.

LoadProcess runs the generator + guard in a separate forked process (recommended live:
no GIL sharing with the responder) and exposes set_level / snapshot / inject / stop.

snapshot() -> {'load_level', 'requested_level', 'target_rps', 'load_observed_rps',
               'host': {'cpu_pct', 'free_ram_mb', 'load_throttled'}, 'completed', 'errors',
               'dropped_ticks', 'events'}
The per-trial record fields are snapshot['load_level'], snapshot['load_observed_rps'], snapshot['host'].
"""
from __future__ import annotations
import collections, hashlib, multiprocessing, queue, random, threading, time
from urllib.parse import quote

from harness_util import LoopbackClient, sleep_until_ns

LOAD_NS = 'e40-load'
LOAD_PREFIX = 'e40-load-'
LOAD_LABEL = ('e40-load', 'true')
LOAD_LEVELS = {'idle': 0.0, 'moderate': 50.0, 'high': 150.0}
LEVEL_ORDER = ('idle', 'moderate', 'high')
DEFAULT_MIX = (('get', 0.6), ('list', 0.2), ('patch', 0.2))
CPU_LIMIT_PCT = 85.0
CPU_SUSTAIN_S = 60.0
MIN_AVAIL_MB = 2048.0


def lower_level(level):
    i = LEVEL_ORDER.index(level)
    return LEVEL_ORDER[max(i - 1, 0)]


def cap_level(level, cap):
    return level if LEVEL_ORDER.index(level) <= LEVEL_ORDER.index(cap) else cap


def load_object_names(n):
    return tuple('%s%02d' % (LOAD_PREFIX, i) for i in range(n))


def make_load_allow(names, namespace=LOAD_NS):
    """Allowlist for the load identity: only its own ConfigMaps in its own namespace."""
    root = f'/api/v1/namespaces/{namespace}/configmaps'
    single = {f'{root}/{n}' for n in names}
    list_path = f'{root}?labelSelector=' + quote('%s=%s' % LOAD_LABEL, safe='')

    def allow(method, path, body):
        if method == 'GET':
            return path in single or path == list_path
        if method == 'PATCH':
            return path in single and isinstance((body or {}).get('data'), dict)
        if method == 'POST':
            md = (body or {}).get('metadata') or {}
            return path == root and md.get('name') in names and md.get('namespace', namespace) == namespace
        return False
    allow.list_path = list_path
    return allow


def load_client_factory(base, names, namespace=LOAD_NS, token_path=None, ca_path=None, identity='load'):
    allow = make_load_allow(names, namespace)
    return lambda: LoopbackClient(base, namespace, identity, token_path=token_path, ca_path=ca_path, allow=allow)


def ensure_objects(client, names, namespace=LOAD_NS):
    """Create the load ConfigMaps if missing (setup step; not timed)."""
    created = 0
    for n in names:
        st, _, _ = client.request('GET', f'/api/v1/namespaces/{namespace}/configmaps/{n}')
        if st == 404:
            body = {'apiVersion': 'v1', 'kind': 'ConfigMap',
                    'metadata': {'name': n, 'namespace': namespace, 'labels': {LOAD_LABEL[0]: LOAD_LABEL[1]}},
                    'data': {'counter': '0'}}
            st, _, _ = client.request('POST', f'/api/v1/namespaces/{namespace}/configmaps', body)
            if st not in (200, 201):
                raise RuntimeError('create %s HTTP %s' % (n, st))
            created += 1
        elif st != 200:
            raise RuntimeError('get %s HTTP %s' % (n, st))
    return created


# --------------------------------------------------------------------------- host guard
def read_proc_stat(path='/proc/stat'):
    with open(path) as fh:
        for line in fh:
            if line.startswith('cpu '):
                v = [int(x) for x in line.split()[1:]]
                idle = v[3] + (v[4] if len(v) > 4 else 0)          # idle + iowait
                total = sum(v[:8])                                    # exclude guest (already in user)
                return idle, total
    raise RuntimeError('no aggregate cpu line in ' + path)


def read_mem_available_mb(path='/proc/meminfo'):
    with open(path) as fh:
        for line in fh:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1024.0
    raise RuntimeError('MemAvailable not found in ' + path)


class HostGuard:
    def __init__(self, cpu_limit_pct=CPU_LIMIT_PCT, cpu_sustain_s=CPU_SUSTAIN_S, min_avail_mb=MIN_AVAIL_MB,
                 stat_path='/proc/stat', meminfo_path='/proc/meminfo', clock=time.monotonic):
        self.cpu_limit_pct = float(cpu_limit_pct)
        self.cpu_sustain_s = float(cpu_sustain_s)
        self.min_avail_mb = float(min_avail_mb)
        self.stat_path, self.meminfo_path = stat_path, meminfo_path
        self.clock = clock
        self._prev = None
        self._inj_cpu = None
        self._inj_mem = None
        self.cpu_high_since = None
        self.mem_low_since = None
        self.last ={'cpu_pct': None, 'free_ram_mb': None}
        self.lock = threading.Lock()

    # --- test hook ---
    def inject(self, cpu_pct=None, mem_available_mb=None):
        """Override /proc readings (None = use the real reading)."""
        with self.lock:
            self._inj_cpu = None if cpu_pct is None else float(cpu_pct)
            self._inj_mem = None if mem_available_mb is None else float(mem_available_mb)

    def _read(self):
        with self.lock:
            inj_cpu, inj_mem = self._inj_cpu, self._inj_mem
        cpu = inj_cpu
        if cpu is None:
            idle, total = read_proc_stat(self.stat_path)
            if self._prev is None:
                cpu = 0.0 if self.last['cpu_pct'] is None else self.last['cpu_pct']
            else:
                d_idle, d_total = idle - self._prev[0], total - self._prev[1]
                cpu = 100.0 * (1.0 - d_idle / d_total) if d_total > 0 else 0.0
            self._prev = (idle, total)
        mem = inj_mem if inj_mem is not None else read_mem_available_mb(self.meminfo_path)
        return cpu, mem

    def sample(self, now=None):
        """Take one reading; returns (snapshot, trigger) where trigger is None or a reason dict."""
        now = self.clock() if now is None else now
        cpu, mem = self._read()
        self.last = {'cpu_pct': round(cpu, 2), 'free_ram_mb': round(mem, 1)}
        trigger = None
        if cpu > self.cpu_limit_pct:
            if self.cpu_high_since is None:
                self.cpu_high_since = now
            elif now - self.cpu_high_since > self.cpu_sustain_s:
                trigger = {'reason': 'cpu_gt_%g_for_gt_%gs' % (self.cpu_limit_pct, self.cpu_sustain_s),
                           'cpu_high_for_s': round(now - self.cpu_high_since, 3)}
                self.cpu_high_since = now        # a further drop needs another sustained period
        else:
            self.cpu_high_since = None
        if mem < self.min_avail_mb:
            # edge-triggered: fire on entering the low-memory state; while it persists, fire again
            # only after another sustained period (so one episode drops ONE level, as the spec says)
            if self.mem_low_since is None or now - self.mem_low_since > self.cpu_sustain_s:
                trigger = {'reason': 'mem_available_lt_%gmb' % self.min_avail_mb}
                self.mem_low_since = now
        else:
            self.mem_low_since = None
        return dict(self.last), trigger

    def precheck(self, settle_s=1.0):
        """Two readings settle_s apart; True if neither CPU > limit nor MemAvailable < min."""
        self.sample()
        time.sleep(settle_s)
        snap, _ = self.sample()
        return snap['cpu_pct'] <= self.cpu_limit_pct and snap['free_ram_mb'] >= self.min_avail_mb, snap


# --------------------------------------------------------------------------- generator
class LoadGenerator:
    def __init__(self, client_factory, names, level='idle', mix=DEFAULT_MIX, n_workers=8, seed=0,
                 guard=None, guard_interval_s=1.0, rps_window_s=5.0, levels=None, namespace=LOAD_NS,
                 log_fn=None, max_backlog=None):
        self.client_factory = client_factory
        self.names = tuple(names)
        self.mix = tuple(mix)
        self.n_workers = int(n_workers)
        self.seed = int(seed)
        self.guard = guard
        self.guard_interval_s = float(guard_interval_s)
        self.rps_window_s = float(rps_window_s)
        self.levels = dict(levels or LOAD_LEVELS)
        self.ns = namespace
        self.log_fn = log_fn
        self.max_backlog = max_backlog or 2 * self.n_workers
        self.requested_level = level
        self.max_level = 'high'
        self.throttled = False
        self.events = []
        self.completed = 0
        self.errors = 0
        self.status_counts = collections.Counter()
        self.op_counts = collections.Counter()
        self.dropped = 0
        self._done_ts = collections.deque()
        self._level_changed = time.monotonic()
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._gen = 0
        self._threads = []
        self.list_path = f'/api/v1/namespaces/{namespace}/configmaps?labelSelector=' + \
            quote('%s=%s' % LOAD_LABEL, safe='')

    # --- level control ---
    @property
    def level(self):
        return cap_level(self.requested_level, self.max_level)

    def target_rps(self):
        return float(self.levels[self.level])

    def set_level(self, level):
        if level not in LEVEL_ORDER:
            raise ValueError('unknown load level %r' % (level,))
        with self._lock:
            self.requested_level = level
            self._gen += 1
            self._level_changed = time.monotonic()
            self._done_ts.clear()
        return self.level

    def reset_throttle(self):
        with self._lock:
            self.max_level = 'high'
            self.throttled = False
            self._gen += 1

    def _throttle(self, trigger, snap):
        with self._lock:
            frm = self.level
            to = lower_level(frm)
            self.max_level = to
            self.throttled = True
            self._gen += 1
            self._level_changed = time.monotonic()
            self._done_ts.clear()
            ev = {'t_wall_ns': time.time_ns(), 't_mono_ns': time.monotonic_ns(), 'from_level': frm,
                  'to_level': to, 'cpu_pct': snap['cpu_pct'], 'free_ram_mb': snap['free_ram_mb']}
            ev.update(trigger)
            self.events.append(ev)
        if self.log_fn:
            try:
                self.log_fn('LOAD THROTTLE %r' % (ev,))
            except Exception:
                pass
        return ev

    def guard_step(self, now=None):
        """One guard evaluation (called by the guard thread every second; callable from tests)."""
        if self.guard is None:
            return None
        snap, trig = self.guard.sample(now=now)
        if trig is not None:
            return self._throttle(trig, snap)
        return None

    # --- threads ---
    def _dispatcher(self):
        next_t = None
        gen = None
        while not self._stop.is_set():
            rps = self.target_rps()
            if rps <= 0:
                next_t = None
                time.sleep(0.05)
                continue
            interval = int(1e9 / rps)
            if gen != self._gen or next_t is None:
                gen = self._gen
                next_t = time.monotonic_ns()
            next_t += interval
            if next_t - time.monotonic_ns() > 5_000_000:
                time.sleep((next_t - time.monotonic_ns() - 2_000_000) / 1e9)
                if gen != self._gen or self._stop.is_set():
                    continue
            sleep_until_ns(next_t, spin_ns=200_000)
            if self._q.qsize() >= self.max_backlog:
                with self._lock:
                    self.dropped += 1
            else:
                self._q.put(next_t)
            lag = time.monotonic_ns() - next_t
            if lag > 50 * interval:          # re-anchor after a long stall instead of bursting
                next_t = time.monotonic_ns()

    def _worker(self, idx):
        rng = random.Random(int.from_bytes(hashlib.sha256(b'e39.D.load|%d|%d' % (self.seed, idx)).digest()[:8], 'big'))
        client = self.client_factory()
        try:
            client.connect()
        except Exception:
            pass
        ops = [m[0] for m in self.mix]
        weights = [m[1] for m in self.mix]
        root = f'/api/v1/namespaces/{self.ns}/configmaps'
        counter = 0
        while not self._stop.is_set():
            try:
                self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            op = rng.choices(ops, weights)[0]
            name = rng.choice(self.names)
            try:
                if op == 'get':
                    st, _, _ = client.request('GET', f'{root}/{name}')
                elif op == 'list':
                    st, _, _ = client.request('GET', self.list_path)
                else:
                    counter += 1
                    st, _, _ = client.request('PATCH', f'{root}/{name}',
                                              {'data': {'counter': '%d-%d' % (idx, counter)}})
                err = st >= 400
            except Exception:
                st, err = 'exc', True
            t = time.monotonic()
            with self._lock:
                self.completed += 1
                self.op_counts[op] += 1
                self.status_counts[str(st)] += 1
                if err:
                    self.errors += 1
                self._done_ts.append(t)
        try:
            client.close()
        except Exception:
            pass

    def _guard_loop(self):
        while not self._stop.wait(self.guard_interval_s):
            try:
                self.guard_step()
            except Exception as exc:
                with self._lock:
                    self.events.append({'t_wall_ns': time.time_ns(), 'reason': 'guard_error', 'error': repr(exc)})

    def start(self):
        for i in range(self.n_workers):
            t = threading.Thread(target=self._worker, args=(i,), name='e40-load-w%d' % i, daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._dispatcher, name='e40-load-dispatch', daemon=True)
        t.start()
        self._threads.append(t)
        if self.guard is not None:
            self.guard.sample()           # prime the /proc/stat delta
            t = threading.Thread(target=self._guard_loop, name='e40-load-guard', daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def observed_rps(self):
        now = time.monotonic()
        with self._lock:
            lo = now - self.rps_window_s
            while self._done_ts and self._done_ts[0] < lo:
                self._done_ts.popleft()
            span = min(self.rps_window_s, now - self._level_changed)
            n = len(self._done_ts)
        return (n / span) if span > 0.2 else None

    def snapshot(self):
        rps = self.observed_rps()
        g = self.guard.last if self.guard is not None else {'cpu_pct': None, 'free_ram_mb': None}
        with self._lock:
            return {'load_level': self.level, 'requested_level': self.requested_level,
                    'target_rps': self.target_rps(),
                    'load_observed_rps': None if rps is None else round(rps, 2),
                    'host': {'cpu_pct': g['cpu_pct'], 'free_ram_mb': g['free_ram_mb'],
                             'load_throttled': self.throttled},
                    'completed': self.completed, 'errors': self.errors, 'dropped_ticks': self.dropped,
                    'op_counts': dict(self.op_counts), 'status_counts': dict(self.status_counts),
                    'events': list(self.events)}


# --------------------------------------------------------------------------- process wrapper
def _load_proc_main(kwargs, conn):
    guard_kwargs = kwargs.pop('guard_kwargs', None)
    guard = HostGuard(**guard_kwargs) if guard_kwargs is not None else None
    gen = LoadGenerator(guard=guard, **kwargs).start()
    conn.send(('started',))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        tag = msg[0]
        try:
            if tag == 'stop':
                break
            elif tag == 'set_level':
                conn.send(('ok', gen.set_level(msg[1])))
            elif tag == 'snapshot':
                conn.send(('ok', gen.snapshot()))
            elif tag == 'inject':
                if guard is None:
                    raise RuntimeError('no guard configured')
                guard.inject(*msg[1:])
                conn.send(('ok', None))
            elif tag == 'reset_throttle':
                gen.reset_throttle()
                conn.send(('ok', None))
            else:
                conn.send(('error', 'unknown command %r' % (tag,)))
        except Exception as exc:
            conn.send(('error', repr(exc)))
    gen.stop()
    conn.send(('stopped', gen.snapshot()))


class LoadProcess:
    """LoadGenerator (+ HostGuard) in a separate forked process.

    guard_kwargs: dict for HostGuard (e.g. {}), or None for no guard.
    Other kwargs as LoadGenerator (client_factory, names, level, mix, n_workers, seed, ...).
    """

    def __init__(self, guard_kwargs=None, **kwargs):
        kwargs['guard_kwargs'] = guard_kwargs
        ctx = multiprocessing.get_context('fork')
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_load_proc_main, args=(kwargs, child), name='e40-load', daemon=True)
        self.proc.start()
        child.close()
        if not self.conn.poll(30) or self.conn.recv()[0] != 'started':
            raise RuntimeError('load process did not start')
        self.final = None

    def _call(self, *msg):
        self.conn.send(msg)
        if not self.conn.poll(30):
            raise RuntimeError('load process not responding')
        tag, val = self.conn.recv()
        if tag != 'ok':
            raise RuntimeError(val)
        return val

    def set_level(self, level):
        return self._call('set_level', level)

    def snapshot(self):
        return self._call('snapshot')

    def inject(self, cpu_pct=None, mem_available_mb=None):
        return self._call('inject', cpu_pct, mem_available_mb)

    def reset_throttle(self):
        return self._call('reset_throttle')

    def stop(self):
        try:
            self.conn.send(('stop',))
            if self.conn.poll(30):
                msg = self.conn.recv()
                if msg[0] == 'stopped':
                    self.final = msg[1]
        except (OSError, EOFError, BrokenPipeError):
            pass
        self.proc.join(timeout=10)
        if self.proc.is_alive():
            self.proc.terminate()
        return self.final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stop()
