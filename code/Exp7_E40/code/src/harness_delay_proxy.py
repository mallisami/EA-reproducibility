#!/usr/bin/env python3
"""E39-C: injected read latency (loopback delay proxy) + paired E21-style acquisition driver.

DelayProxy
----------
A forwarding HTTP/1.1 proxy that listens ONLY on 127.0.0.1 and forwards ONLY to a
127.0.0.1 upstream (the kind apiserver). The RESPONDER identity is pointed at the proxy;
all other identities (writer, reset, webhook, load) talk to the apiserver directly.

  * delay: ``delay_ms`` is added to single-object ConfigMap GETs only
    (GET /api/v1/namespaces/<ns>/configmaps/<name>); LIST, watch, POST, PATCH, DELETE,
    SSAR, Pod GETs etc. pass through untouched.
    delay_position: 'before' (default; request held c ms before it is forwarded, so the
    read reflects state at t_recv + c), 'after' (response held c ms), 'split' (c/2 each).
    Timing uses coarse sleep + 1 ms busy-spin against an absolute monotonic deadline.
  * headers: end-to-end headers are forwarded both ways unchanged (Authorization,
    User-Agent, Content-Type, Audit-Id, ...); hop-by-hop headers are dropped/regenerated.
  * keep-alive: each downstream connection gets its own persistent upstream connection.
  * TLS: listener may serve TLS (tls_cert/tls_key, so the responder keeps its pinned-CA
    https client with bearer token); upstream TLS verifies the apiserver against a
    PINNED CA file (upstream_ca) with hostname 127.0.0.1.
  * chunked upstream responses (e.g. watches) are streamed through unchanged.

DelayProxyProcess runs the proxy in a separate forked process (recommended live, so the
proxy's sleeping/spinning threads never share a GIL with the responder).

calibrate(): measures the added delay vs requested (through the proxy with c vs c=0,
and vs direct upstream) and reports error statistics.

PairedAcquisitionDriver
-----------------------
E21-style paired guard comparison on six evidence ConfigMaps (evidence-a..f):
DCJI reads the planner's certificate evidence (3 reads: a, c, e), FullRefresh reads all
six. acquisition_mode sequential (one keep-alive connection) or parallel (one pre-opened
keep-alive connection per read, reads issued concurrently); planning_mode precomputed
(certificate computed once at driver construction) or per_incident (planner is run
inside the timed DCJI guard, planning_ms recorded). Guard order per pair is randomized
in permuted blocks (balanced). Output: two records per pair with the schema fields
experiment, arm, injected_latency_ms, acquisition_mode, planning_mode, planning_ms,
guard_order, pair_id plus an ``acquisition`` block (reads, n_reads, refresh/gate timing).
"""
from __future__ import annotations
import hashlib, http.client, multiprocessing, random, re, socket, ssl, statistics, threading, time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness_util import check_loopback_url, sleep_until_ns

CM_GET_RE = re.compile(r'^/api/v1/namespaces/[^/?]+/configmaps/[^/?]+(\?.*)?$')
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
              'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length', 'proxy-connection'}
DELAY_POSITIONS = ('before', 'after', 'split')
LATENCY_GRID_MS = (0.0, 50.0, 200.0, 1000.0)
LOG_LIMIT = 200000


def is_configmap_get(method, path):
    if method != 'GET' or not CM_GET_RE.match(path):
        return False
    q = path.split('?', 1)[1] if '?' in path else ''
    return 'watch=' not in q     # a single-object watch is streaming, never delayed


# --------------------------------------------------------------------------- proxy
class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True     # TCP_NODELAY downstream: no Nagle/delayed-ACK 40 ms stalls

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        self._up = None

    def finish(self):
        try:
            super().finish()
        finally:
            if self._up is not None:
                try:
                    self._up.close()
                except Exception:
                    pass
                self._up = None

    def _new_upstream(self):
        p = self.server.proxy
        if p.up_scheme == 'https':
            c = http.client.HTTPSConnection('127.0.0.1', p.up_port, timeout=p.timeout_s, context=p.up_ssl)
        else:
            c = http.client.HTTPConnection('127.0.0.1', p.up_port, timeout=p.timeout_s)
        c.connect()
        c.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with p.lock:
            p.upstream_connections_opened += 1
        return c

    def _forward(self):
        p = self.server.proxy
        t_recv = time.monotonic_ns()
        method, path = self.command, self.path
        n = int(self.headers.get('Content-Length', 0) or 0)
        body = self.rfile.read(n) if n else None
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            self.send_error(501, 'chunked request bodies not supported by e39 delay proxy')
            return
        delay_ns = p.delay_ns
        delayed = delay_ns > 0 and is_configmap_get(method, path) and p.ua_matches(self.headers.get('User-Agent', ''))
        before_ns = after_ns = 0
        if delayed:
            if p.delay_position == 'before':
                before_ns = delay_ns
            elif p.delay_position == 'after':
                after_ns = delay_ns
            else:
                before_ns = delay_ns // 2
                after_ns = delay_ns - before_ns
        if before_ns:
            sleep_until_ns(t_recv + before_ns)
        t_fwd = time.monotonic_ns()
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers['Host'] = '127.0.0.1:%d' % p.up_port
        if body is not None:
            headers['Content-Length'] = str(len(body))
        resp = None
        for attempt in (0, 1):
            reused = self._up is not None
            try:
                if self._up is None:
                    self._up = self._new_upstream()
                self._up.request(method, path, body=body, headers=headers)
                resp = self._up.getresponse()
                break
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.CannotSendRequest):
                try:
                    if self._up is not None:
                        self._up.close()
                finally:
                    self._up = None
                if attempt == 0 and reused:
                    continue
                self.send_error(502, 'upstream connection failed')
                return
            except (http.client.HTTPException, OSError):
                try:
                    if self._up is not None:
                        self._up.close()
                finally:
                    self._up = None
                self.send_error(502, 'upstream error')
                return
        streaming = resp.getheader('Content-Length') is None and resp.chunked and method != 'HEAD'
        data = None if streaming else resp.read()
        t_up_done = time.monotonic_ns()
        if after_ns:
            sleep_until_ns(t_up_done + after_ns)
        self.send_response_only(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        if streaming:
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b'%x\r\n%s\r\n' % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
        else:
            self.send_header('Content-Length', str(len(data)))
            # headers + body in ONE write (same effect as end_headers() + write, one segment)
            self._headers_buffer.append(b'\r\n')
            if data and method != 'HEAD':
                self._headers_buffer.append(data)
            self.flush_headers()
        self.wfile.flush()
        t_sent = time.monotonic_ns()
        if resp.will_close and self._up is not None:
            self._up.close()
            self._up = None
        p.log_request({'method': method, 'path': path, 'status': resp.status, 'delayed': bool(delayed),
                       'requested_delay_ms': delay_ns / 1e6 if delayed else 0.0,
                       'before_ns': before_ns, 'after_ns': after_ns,
                       'applied_before_ns': t_fwd - t_recv,
                       'upstream_ns': t_up_done - t_fwd, 'total_ns': t_sent - t_recv,
                       'audit_id': resp.getheader('Audit-Id'), 't_recv_mono_ns': t_recv})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = _forward


class DelayProxy:
    """In-process delay proxy (threads). Use DelayProxyProcess for live runs."""

    def __init__(self, upstream_base, delay_ms=0.0, delay_position='before', upstream_ca=None,
                 tls_cert=None, tls_key=None, listen_port=0, match_user_agent=None, timeout_s=30.0):
        up = check_loopback_url(upstream_base)
        if delay_position not in DELAY_POSITIONS:
            raise ValueError('delay_position must be one of %r' % (DELAY_POSITIONS,))
        self.up_scheme, self.up_port = up.scheme, up.port
        self.up_ssl = None
        if self.up_scheme == 'https':
            if not upstream_ca:
                raise ValueError('https upstream requires a pinned CA file (upstream_ca)')
            ctx = ssl.create_default_context(cafile=upstream_ca)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            self.up_ssl = ctx
        self.delay_position = delay_position
        self.match_user_agent = match_user_agent
        self.timeout_s = timeout_s
        self.lock = threading.Lock()
        self.log = []
        self.upstream_connections_opened = 0
        self.set_delay_ms(delay_ms)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', int(listen_port)), _ProxyHandler)
        self.httpd.daemon_threads = True
        self.httpd.proxy = self
        self.tls = bool(tls_cert)
        if self.tls:
            sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            sctx.minimum_version = ssl.TLSVersion.TLSv1_2
            sctx.load_cert_chain(tls_cert, tls_key)
            self.httpd.socket = sctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = None

    @property
    def base_url(self):
        return '%s://127.0.0.1:%d' % ('https' if self.tls else 'http', self.port)

    def set_delay_ms(self, delay_ms):
        delay_ms = float(delay_ms)
        if delay_ms < 0:
            raise ValueError('delay must be >= 0')
        self.delay_ms = delay_ms
        self.delay_ns = int(round(delay_ms * 1e6))

    def ua_matches(self, ua):
        return self.match_user_agent is None or self.match_user_agent in ua

    def log_request(self, rec):
        with self.lock:
            if len(self.log) < LOG_LIMIT:
                self.log.append(rec)

    def take_log(self, clear=True):
        with self.lock:
            out = list(self.log)
            if clear:
                self.log.clear()
        return out

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='e40-delay-proxy', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def _proxy_proc_main(kwargs, conn):
    try:
        proxy = DelayProxy(**kwargs).start()
    except Exception as exc:
        conn.send(('error', repr(exc)))
        return
    conn.send(('port', proxy.port, proxy.base_url))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg[0] == 'stop':
            break
        if msg[0] == 'set_delay':
            try:
                proxy.set_delay_ms(msg[1])
                conn.send(('ok', proxy.delay_ms))
            except Exception as exc:
                conn.send(('error', repr(exc)))
        elif msg[0] == 'take_log':
            conn.send(('log', proxy.take_log(clear=msg[1])))
        elif msg[0] == 'stats':
            conn.send(('stats', {'delay_ms': proxy.delay_ms,
                                 'upstream_connections_opened': proxy.upstream_connections_opened}))
    proxy.stop()


class DelayProxyProcess:
    """DelayProxy in a separate forked process, controlled through a pipe."""

    def __init__(self, upstream_base, **kwargs):
        kwargs['upstream_base'] = upstream_base
        ctx = multiprocessing.get_context('fork')
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_proxy_proc_main, args=(kwargs, child), name='e40-delay-proxy', daemon=True)
        self.proc.start()
        child.close()
        if not self.conn.poll(30):
            raise RuntimeError('delay proxy process did not start')
        msg = self.conn.recv()
        if msg[0] != 'port':
            raise RuntimeError('delay proxy failed: %r' % (msg,))
        self.port, self.base_url = msg[1], msg[2]

    def _call(self, *msg):
        self.conn.send(msg)
        if not self.conn.poll(30):
            raise RuntimeError('delay proxy process not responding')
        return self.conn.recv()

    def set_delay_ms(self, c):
        r = self._call('set_delay', float(c))
        if r[0] != 'ok':
            raise RuntimeError(r[1])
        return r[1]

    def take_log(self, clear=True):
        return self._call('take_log', clear)[1]

    def stats(self):
        return self._call('stats')[1]

    def stop(self):
        try:
            self.conn.send(('stop',))
        except (OSError, EOFError, BrokenPipeError):
            pass
        self.proc.join(timeout=10)
        if self.proc.is_alive():
            self.proc.terminate()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stop()


# --------------------------------------------------------------------------- calibration
def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _timed_gets(client, path, n):
    out = []
    for _ in range(n):
        t0 = time.monotonic_ns()
        status, _, _ = client.request('GET', path)
        out.append((time.monotonic_ns() - t0) / 1e6)
        if status != 200:
            raise RuntimeError('calibration GET %s HTTP %s' % (path, status))
    return out


def calibrate(proxy, proxy_client, direct_client, path, delays_ms=LATENCY_GRID_MS, n=30, warmup=5):
    """Measure added delay vs requested for each c in delays_ms.

    proxy: DelayProxy or DelayProxyProcess (set_delay_ms); proxy_client / direct_client:
    keep-alive clients (request(method, path)) pointed at the proxy / upstream.
    Returns {'baseline_direct_ms':..., 'baseline_proxy0_ms':..., 'proxy_overhead_ms':...,
             'per_delay': [{requested_ms, mean_added_ms, median_added_ms, err_mean_ms,
                            err_p95_abs_ms, err_max_abs_ms, n}]}
    added = latency(proxy, c) - median latency(proxy, c=0).
    """
    _timed_gets(direct_client, path, warmup)
    direct = _timed_gets(direct_client, path, n)
    proxy.set_delay_ms(0)
    _timed_gets(proxy_client, path, warmup)
    base = _timed_gets(proxy_client, path, n)
    base_med = statistics.median(base)
    out = {'baseline_direct_ms': statistics.median(direct), 'baseline_proxy0_ms': base_med,
           'proxy_overhead_ms': base_med - statistics.median(direct), 'n': n, 'per_delay': []}
    for c in delays_ms:
        proxy.set_delay_ms(c)
        _timed_gets(proxy_client, path, 1)
        lat = _timed_gets(proxy_client, path, n)
        added = [x - base_med for x in lat]
        errs = [a - c for a in added]
        out['per_delay'].append({'requested_ms': float(c), 'mean_added_ms': statistics.fmean(added),
                                 'median_added_ms': statistics.median(added),
                                 'err_mean_ms': statistics.fmean(errs),
                                 'err_p95_abs_ms': _pct([abs(e) for e in errs], 0.95),
                                 'err_max_abs_ms': max(abs(e) for e in errs), 'n': n})
    proxy.set_delay_ms(0)
    return out


# --------------------------------------------------------------------------- paired acquisition
FLAGS = ('a', 'b', 'c', 'd', 'e', 'f')
EVIDENCE_CMS = tuple('evidence-' + k for k in FLAGS)
ARM_DCJI = 'A1_dcji'
ARM_FULL = 'A2_fullrefresh'
ACQ_MODES = ('sequential', 'parallel')
PLANNING_MODES = ('precomputed', 'per_incident')
GUARD_ORDERS = ('dcji_first', 'fullrefresh_first')


def justification(ev):
    return (ev['a'] and ev['b']) or (ev['c'] and ev['d']) or (ev['e'] and ev['f'])


def make_dcji_planner(f5_path, horizon_s=30.0, attacker_capability='C2', profile='balanced'):
    """Planner callable -> evidence flags DCJI must re-read (expected ('a','c','e')).

    Runs flashir_core.dcji.derive_critical_predicates on the E38v2 model built from the E37
    F5 physical scenario (the same planner and model as e38v2_model.frozen_planner_certificate,
    without its multi-horizon invariance re-checks, which are a one-time session check)."""
    import json
    from pathlib import Path
    from flashir_core.dcji import derive_critical_predicates
    from e38v2_model import construct_model, PREDICATE_TO_PHYSICAL
    f5 = json.loads(Path(f5_path).read_text())

    def plan():
        model = construct_model(f5)
        res = derive_critical_predicates(model, float(horizon_s), attacker_capability, profile)
        if res['status'] != 'EXPLOITABLE':
            raise RuntimeError('planner status %s' % res['status'])
        phys = {PREDICATE_TO_PHYSICAL[p] for p in res['critical_predicates']}
        return tuple(k for k in FLAGS if k in phys)
    return plan


def guard_orders(seed, n_pairs, block_size=4):
    """Balanced randomized guard order: permuted blocks with half of each order per block."""
    if block_size % 2:
        raise ValueError('block_size must be even')
    material = 'e39.C.guard_order|%d|%d|%d' % (int(seed), int(n_pairs), int(block_size))
    rng = random.Random(int.from_bytes(hashlib.sha256(material.encode()).digest()[:16], 'big'))
    out = []
    while len(out) < n_pairs:
        blk = [GUARD_ORDERS[0]] * (block_size // 2) + [GUARD_ORDERS[1]] * (block_size // 2)
        rng.shuffle(blk)
        out.extend(blk)
    return out[:n_pairs]


def _read_cm(client, name):
    t_send = time.monotonic_ns()
    status, headers, obj = client.get_configmap(name)
    t_recv = time.monotonic_ns()
    data = (obj or {}).get('data') or {}
    return {'object': name, 'resource_version': ((obj or {}).get('metadata') or {}).get('resourceVersion'),
            'value': data.get('value', data.get('allowed')), 'http_status': status,
            'audit_id': headers.get('Audit-Id'), 't_send_mono_ns': t_send, 't_recv_mono_ns': t_recv}


class PairedAcquisitionDriver:
    """E21-style paired DCJI (3 reads) vs FullRefresh (6 reads) acquisition.

    client_factory(): new keep-alive client for the RESPONDER identity pointed at the delay
    proxy; must provide connect() and get_configmap(name) -> (status, headers, obj).
    planner(): tuple of evidence flags for DCJI (e.g. from make_dcji_planner).
    before_guard(arm, pair_id, position): optional hook (e.g. set stable / ea_attack fixture).
    """

    def __init__(self, client_factory, planner, acquisition_mode, planning_mode, injected_latency_ms,
                 seed, n_pairs, block_size=4, before_guard=None, cached=None):
        if acquisition_mode not in ACQ_MODES or planning_mode not in PLANNING_MODES:
            raise ValueError('bad acquisition/planning mode')
        self.planner = planner
        self.acquisition_mode = acquisition_mode
        self.planning_mode = planning_mode
        self.injected_latency_ms = float(injected_latency_ms)
        self.orders = guard_orders(seed, n_pairs, block_size)
        self.before_guard = before_guard
        self.cached = dict(cached or {k: True for k in FLAGS})
        n_clients = len(FLAGS) if acquisition_mode == 'parallel' else 1
        self.clients = [client_factory().connect() for _ in range(n_clients)]
        self.pool = ThreadPoolExecutor(max_workers=n_clients) if acquisition_mode == 'parallel' else None
        if self.pool:     # pre-start worker threads
            list(self.pool.map(lambda _: None, range(n_clients * 2)))
        self.precomputed_flags = None
        self.precompute_ms = None
        if planning_mode == 'precomputed':
            t0 = time.monotonic_ns()
            self.precomputed_flags = tuple(planner())
            self.precompute_ms = (time.monotonic_ns() - t0) / 1e6

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True)
        for c in self.clients:
            try:
                c.close()
            except Exception:
                pass

    def _acquire(self, names):
        if self.acquisition_mode == 'sequential':
            return [_read_cm(self.clients[0], n) for n in names]
        futs = [self.pool.submit(_read_cm, self.clients[i], n) for i, n in enumerate(names)]
        return [f.result() for f in futs]

    def run_guard(self, arm):
        t_start = time.monotonic_ns()
        planning_ms = None
        if arm == ARM_DCJI:
            if self.planning_mode == 'per_incident':
                flags = tuple(self.planner())
                planning_ms = (time.monotonic_ns() - t_start) / 1e6
            else:
                flags = self.precomputed_flags
        else:
            flags = FLAGS
        t_plan_end = time.monotonic_ns()
        reads = self._acquire(['evidence-' + k for k in flags])
        t_end = time.monotonic_ns()
        measured = {r['object'][-1]: (r['value'] == 'true') for r in reads}
        hybrid = {k: measured.get(k, self.cached[k]) for k in FLAGS}
        return {'arm': arm, 'planning_ms': planning_ms, 'flags_read': list(flags), 'reads': reads,
                'n_reads': len(reads), 't_start_mono_ns': t_start, 't_plan_end_mono_ns': t_plan_end,
                't_end_mono_ns': t_end, 'refresh_ms': (t_end - t_plan_end) / 1e6,
                'gate_ms': (t_end - t_start) / 1e6, 'justified': bool(justification(hybrid)),
                'all_reads_ok': all(r['http_status'] == 200 for r in reads)}

    def run_pair(self, pair_id):
        order = self.orders[pair_id]
        arms = (ARM_DCJI, ARM_FULL) if order == 'dcji_first' else (ARM_FULL, ARM_DCJI)
        out = []
        for pos, arm in enumerate(arms):
            if self.before_guard:
                self.before_guard(arm, pair_id, pos)
            g = self.run_guard(arm)
            out.append({'experiment': 'C', 'arm': arm, 'pair_id': int(pair_id), 'guard_order': order,
                        'position_in_pair': pos, 'injected_latency_ms': self.injected_latency_ms,
                        'acquisition_mode': self.acquisition_mode, 'planning_mode': self.planning_mode,
                        'planning_ms': g.pop('planning_ms'), 'acquisition': g})
        return out

    def run_all(self):
        recs = []
        for i in range(len(self.orders)):
            recs.extend(self.run_pair(i))
        return recs
