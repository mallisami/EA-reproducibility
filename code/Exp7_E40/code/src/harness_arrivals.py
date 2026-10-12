#!/usr/bin/env python3
"""E39-B: Poisson writer arrivals (schedule generator + timed release driver).

Schedule
--------
``poisson_schedule(seed, trial_id, lambda_per_s, window_ms)`` returns the planned
arrival offsets (ms, relative to the ANCHOR = gate start) of a homogeneous Poisson
process with rate lambda on [0, window_ms). The RNG is seeded from
sha256("e39.B.arrivals|<seed>|<trial_id>|<repr(float(lambda))>") so the schedule is a
pure function of (seed, trial_id, lambda, window_ms); it can be generated at schedule
build time and written into the LOCKED schedule file, or regenerated at run time.
Offsets are rounded to 1 us.

Driver
------
Same mechanism as writer.py (ProcessPostGateWriter / WriterProcess): a persistent,
separately forked writer process owns the writer identity's keep-alive connection for
the whole run (no GIL coupling with the responder). Per trial:

    wp = ArrivalWriterProcess(client_factory)           # once per run, BEFORE parent connects
    aw = ArrivalWriter(wp, obj, key, value, offsets_ms, window_ms)
    aw.prearm()                  # job sent, child ready and waiting (connection open)
    aw.arm(t_gate_start_mono_ns) # anchor = gate start; call immediately before the gate
    ... gate ... DELETE ...
    aw.halt(t_delete_resp_mono_ns + 50 ms)  # arrivals planned after this are NOT released
    res = aw.join()              # {'writer_arrivals': [...], 'meta': {...}}

The child releases arrivals sequentially on ONE keep-alive connection. If a PATCH
takes longer than the gap to the next planned arrival, the next release is late; both
intended and actual release times are logged, and the oracle must use actual commit
order (audit log), not planned offsets. Arrival records (schema writer_arrivals items):
    {planned_offset_ms, intended_release_mono_ns, actual_release_mono_ns, t_resp_mono_ns,
     http_status, resulting_resource_version, audit_id, object, new_value[, error]}
(intended_release_mono_ns and new_value mirror the e38v2 writer record; they are
additions to the minimal item list in E39_RECORD_SCHEMA.json.)

Every arrival writes the same invalidating value (first arrival invalidates). On a
real apiserver a merge-patch that changes nothing is a no-op (no new resourceVersion);
pass ``seq_key='e40-arrival-seq'`` to make each arrival a distinct commit (it adds a
data key carrying the arrival index; reset must tolerate the extra key).
"""
from __future__ import annotations
import hashlib, math, multiprocessing, random, time

from harness_util import SPIN_WINDOW_NS

SCHEDULE_TAG = 'e39.B.arrivals'
DEFAULT_WINDOW_MS = 2000.0          # W_max: must exceed gate + DELETE + 50 ms by a wide margin
LAMBDA_GRID = (1.0, 10.0, 100.0)
HALT_GRACE_MS = 50.0                 # spec: arrivals continue until DELETE completes + 50 ms
MAX_ARRIVALS = 100000
POLL_SLICE_NS = 2_000_000

# condition -> (object, data key, invalidating value); async_scope is the E39-B condition
ARRIVAL_TARGETS = {'async_scope': ('response-scope', 'allowed', 'false'),
                   'poisson_scope': ('response-scope', 'allowed', 'false'),
                   'async_app_policy': ('application-policy', 'allowed', 'false'),
                   'async_evidence_last_literal': ('evidence-a', 'value', 'false')}


# --------------------------------------------------------------------------- schedule
def schedule_rng(seed, trial_id, lambda_per_s):
    material = '%s|%d|%d|%r' % (SCHEDULE_TAG, int(seed), int(trial_id), float(lambda_per_s))
    return random.Random(int.from_bytes(hashlib.sha256(material.encode()).digest()[:16], 'big'))


def poisson_schedule(seed, trial_id, lambda_per_s, window_ms=DEFAULT_WINDOW_MS, max_arrivals=MAX_ARRIVALS):
    """Planned arrival offsets (ms from gate start) of a Poisson(lambda) process on [0, window_ms)."""
    lam = float(lambda_per_s)
    if not (lam > 0 and math.isfinite(lam)):
        raise ValueError('lambda_per_s must be a positive finite number')
    if not window_ms > 0:
        raise ValueError('window_ms must be positive')
    rng = schedule_rng(seed, trial_id, lam)
    out, t_ms = [], 0.0
    while len(out) < max_arrivals:
        t_ms += rng.expovariate(lam) * 1000.0
        if t_ms >= window_ms:
            break
        out.append(round(t_ms, 3))
    return out


def predicted_invalid_probability(lambda_per_s, window_s):
    """Model prediction for E39-B: P(at least one arrival inside the exposure window W)."""
    return 1.0 - math.exp(-float(lambda_per_s) * float(window_s))


# --------------------------------------------------------------------------- child process
def _patch(client, target_kind, obj, key, value, seq_key, index, pod_uid=None):
    data = {key: value}
    if seq_key:
        data[seq_key] = str(index)
    if target_kind == 'configmap':
        return client.patch_configmap(obj, data)
    if target_kind == 'pod_annotation':
        # E39-B A4d: merge-PATCH of the TARGET Pod's annotations through e39_kube.E39Client
        # (signature patch_pod_annotations(annotations, uid=None, resource_version=None); the client is
        # bound to the target Pod name). key = annotation key (e.g. e38.scope); seq_key = e38.seq
        # (allowlisted in E39Client); optional uid precondition so a replacement Pod is never written.
        if not hasattr(client, 'patch_pod_annotations'):
            raise ValueError('pod_annotation arrivals need an E39Client (patch_pod_annotations)')
        return client.patch_pod_annotations(data, uid=pod_uid)
    raise ValueError('unknown target_kind %r' % (target_kind,))


def _release(client, job, index, planned_ms, intended, actual):
    rec = {'planned_offset_ms': planned_ms, 'intended_release_mono_ns': int(intended),
           'actual_release_mono_ns': int(actual), 't_resp_mono_ns': None, 'http_status': None,
           'resulting_resource_version': None, 'audit_id': None,
           'object': job['object'], 'new_value': str(job['value'])}
    try:
        status, headers, body = _patch(client, job['target_kind'], job['object'], job['key'],
                                       str(job['value']), job.get('seq_key'), index, job.get('pod_uid'))
        rec['t_resp_mono_ns'] = time.monotonic_ns()
        rec['http_status'] = status
        rec['audit_id'] = headers.get('Audit-Id')
        if isinstance(body, dict):
            rec['resulting_resource_version'] = (body.get('metadata') or {}).get('resourceVersion')
    except Exception as exc:     # never propagate
        rec['t_resp_mono_ns'] = time.monotonic_ns()
        rec['error'] = repr(exc)
    return rec


def _run_job(client, conn, job, anchor):
    offsets_ms = job['offsets_ms']
    hard_end = anchor + int(round(float(job['window_ms']) * 1e6))
    halt_deadline = None
    arrivals = []

    def check_msg(timeout_s):
        nonlocal halt_deadline
        if conn.poll(timeout_s):
            msg = conn.recv()
            if msg[0] == 'halt' and halt_deadline is None:
                halt_deadline = int(msg[1])

    for i, off in enumerate(offsets_ms):
        target = anchor + int(round(off * 1e6))
        if target >= hard_end:
            break
        stop = False
        while True:
            if halt_deadline is not None and target > halt_deadline:
                stop = True
                break
            rem = target - time.monotonic_ns()
            if rem <= 0:
                break
            if rem > SPIN_WINDOW_NS:
                check_msg(min(rem - SPIN_WINDOW_NS, POLL_SLICE_NS) / 1e9)
        if stop:
            break
        actual = time.monotonic_ns()
        arrivals.append(_release(client, job, i, off, target, actual))
        check_msg(0)
    # no more planned arrivals (or halted): wait for halt so the parent can pair it (bounded by window)
    while halt_deadline is None and time.monotonic_ns() < hard_end:
        check_msg(min(POLL_SLICE_NS, max(hard_end - time.monotonic_ns(), 0)) / 1e9)
    released_planned = sum(1 for o in offsets_ms if anchor + int(round(o * 1e6)) < hard_end
                           and (halt_deadline is None or anchor + int(round(o * 1e6)) <= halt_deadline))
    return {'writer_arrivals': arrivals,
            'meta': {'anchor_mono_ns': anchor, 'window_ms': float(job['window_ms']),
                     'halt_deadline_mono_ns': halt_deadline, 'planned_total': len(offsets_ms),
                     'planned_before_halt': released_planned, 'released': len(arrivals),
                     'late_releases_gt_1ms': sum(1 for a in arrivals
                                                 if a['actual_release_mono_ns'] - a['intended_release_mono_ns'] > 1_000_000),
                     'errors': sum(1 for a in arrivals if a.get('error'))}}


def _arrivals_main(client_factory, conn):
    client = client_factory()
    try:
        client.connect()
        client_err = None
    except Exception as exc:
        client_err = repr(exc)
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        tag = msg[0]
        if tag == 'stop':
            break
        if tag == 'halt':          # stale halt for a job that already finished: ignore
            continue
        if tag == 'ping':
            conn.send(('pong', client_err))
            continue
        if tag != 'job':
            conn.send(('error', 'protocol: expected job, got %r' % (tag,)))
            continue
        job = msg[1]
        try:
            client.connect()
        except Exception:
            pass
        conn.send(('ready',))
        arm = conn.recv()
        while arm[0] == 'halt':
            arm = conn.recv()
        if arm[0] == 'cancel':
            conn.send(('cancelled',))
            continue
        if arm[0] != 'arm':
            conn.send(('error', 'protocol: expected arm'))
            continue
        conn.send(('result', _run_job(client, conn, job, int(arm[1]))))
    try:
        client.close()
    except Exception:
        pass


class ArrivalWriterProcess:
    """Persistent forked arrivals writer process. Create BEFORE the parent opens its own connections."""

    def __init__(self, client_factory):
        ctx = multiprocessing.get_context('fork')
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_arrivals_main, args=(client_factory, child),
                                name='e40-arrivals-writer', daemon=True)
        self.proc.start()
        child.close()
        self.busy = False

    def ping(self, timeout=30):
        self.conn.send(('ping',))
        if not self.conn.poll(timeout):
            raise RuntimeError('arrivals writer did not answer ping')
        return self.conn.recv()[1]

    def alive(self):
        return self.proc.is_alive()

    def stop(self):
        try:
            self.conn.send(('stop',))
        except (OSError, EOFError, BrokenPipeError):
            pass
        self.proc.join(timeout=10)
        if self.proc.is_alive():
            self.proc.terminate()


class ArrivalWriter:
    """Per-trial handle (prearm / arm / halt / join / cancel)."""

    def __init__(self, wproc, obj, key, value, offsets_ms, window_ms=DEFAULT_WINDOW_MS,
                 target_kind='configmap', seq_key=None, lambda_per_s=None, pod_uid=None):
        self.wproc = wproc
        self.job = {'object': obj, 'key': key, 'value': str(value), 'offsets_ms': list(offsets_ms),
                    'window_ms': float(window_ms), 'target_kind': target_kind, 'seq_key': seq_key,
                    'pod_uid': pod_uid}
        self.lambda_per_s = lambda_per_s
        self.window_ms = float(window_ms)
        self.result = None
        self._state = 'new'

    @classmethod
    def for_trial(cls, wproc, condition, seed, trial_id, lambda_per_s, window_ms=DEFAULT_WINDOW_MS,
                  target_kind='configmap', seq_key=None, target=None, pod_uid=None):
        obj, key, value = target or ARRIVAL_TARGETS[condition]
        offs = poisson_schedule(seed, trial_id, lambda_per_s, window_ms)
        return cls(wproc, obj, key, value, offs, window_ms, target_kind, seq_key, lambda_per_s, pod_uid)

    def prearm(self, timeout=30):
        if self.wproc.busy:
            raise RuntimeError('arrivals writer busy (previous job not collected)')
        self.wproc.busy = True
        self.wproc.conn.send(('job', self.job))
        if not self.wproc.conn.poll(timeout):
            raise RuntimeError('arrivals writer not ready')
        msg = self.wproc.conn.recv()
        if msg[0] != 'ready':
            raise RuntimeError('arrivals writer: %r' % (msg,))
        self._state = 'ready'
        return self

    def arm(self, anchor_ns):
        """anchor = gate start (monotonic ns). Non-blocking."""
        if self._state != 'ready':
            return
        self.anchor_ns = int(anchor_ns)
        self.wproc.conn.send(('arm', self.anchor_ns))
        self._state = 'armed'

    def halt(self, deadline_ns):
        """No arrival planned after deadline_ns (monotonic) is released. Non-blocking."""
        if self._state == 'armed':
            self.wproc.conn.send(('halt', int(deadline_ns)))
            self._state = 'halted'

    def halt_after_delete(self, t_delete_resp_mono_ns, grace_ms=HALT_GRACE_MS):
        self.halt(int(t_delete_resp_mono_ns) + int(round(grace_ms * 1e6)))

    def join(self, timeout=None):
        if self._state in ('armed', 'halted'):
            if self._state == 'armed':
                self.halt(time.monotonic_ns())
            timeout = timeout if timeout is not None else self.window_ms / 1000.0 + 30
            if not self.wproc.conn.poll(timeout):
                raise RuntimeError('arrivals result not received within %ss' % timeout)
            msg = self.wproc.conn.recv()
            if msg[0] == 'result':
                self.result = msg[1]
                self.result['meta']['lambda_per_s'] = self.lambda_per_s
            self._state = 'done'
            self.wproc.busy = False
        return self.result

    def cancel(self):
        if self._state == 'ready':
            self.wproc.conn.send(('cancel',))
            if self.wproc.conn.poll(30):
                self.wproc.conn.recv()
            self._state = 'done'
            self.wproc.busy = False
        elif self._state in ('armed', 'halted'):
            self.join()

    @property
    def writer_arrivals(self):
        return None if self.result is None else self.result['writer_arrivals']
