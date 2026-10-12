#!/usr/bin/env python3
"""E38v2 asynchronous post-gate writer (FEATURE 2 / FIX (c), (e)).

A single independent write to one justification/scope/policy ConfigMap, issued by a
writer that owns its OWN keep-alive connection (a distinct writer identity, separate
from the responder and reset identities). The writer is PRE-ARMED before the gate:
its connection is open and it is waiting before the responder starts its timed reads,
so releasing the write costs no connection setup and never blocks the responder path
(fix c: no writer activity inside the timed gate read loop -- the responder only hands
over an anchor timestamp).

Two implementations with the same interface (prearm / arm / join / cancel / result /
negative):

  * ProcessPostGateWriter (used by the live runner and the offline self-test): the write
    is executed by a persistent, separately forked WRITER PROCESS (WriterProcess).
    E38v2 integration fix: in a single CPython process a busy-waiting writer THREAD holds
    the GIL and can delay the responder's DELETE by up to the interpreter switch interval
    (5 ms by default) -- the same order as the delta grid. A separate process removes
    that coupling; CLOCK_MONOTONIC is system-wide, so timestamps stay comparable.
  * PostGateWriter (thread; kept for unit tests / reference).

Release timing
--------------
``delta_ms`` is defined relative to the gate end (the moment the responder receives
the response to its final fence read), per the interface spec DELTA GRID.

  * delta_ms >= 0 : anchor = gate_end (t_end_mono_ns). The responder calls
      ``arm(anchor=gate_end)`` immediately after the gate completes; the writer
      busy-waits on time.monotonic_ns() until anchor + delta_ms*1e6, then fires.
  * delta_ms < 0  : anchor = final_read_start (t_send of the final fence read),
      passed via ``arm(anchor=final_read_start)`` immediately BEFORE the final fence
      read is issued. The write is dispatched as soon as it is armed; the achievable
      separation among {-1,-2,-5} ms is bounded by dispatch latency (documented
      limitation; the oracle uses logged actual times and committed resourceVersions).

The intended release is always logged alongside the actual release.
``new_value`` in the result is the written STRING value (interface spec: str).
"""
from __future__ import annotations
import multiprocessing, os, threading, time

SPIN_WINDOW_NS = 2_000_000


def _wait_until(target):
    """Coarse sleep while far away, tight spin for the final ~2 ms."""
    while True:
        now = time.monotonic_ns()
        remaining = target - now
        if remaining <= 0:
            return
        if remaining > SPIN_WINDOW_NS:
            time.sleep(min(remaining - SPIN_WINDOW_NS, 5_000_000) / 1e9)


def _do_patch(client, obj, key, value, intended, actual):
    rec = {'object': obj, 'new_value': value,
           'intended_release_mono_ns': int(intended), 'actual_release_mono_ns': int(actual),
           't_resp_mono_ns': None, 'http_status': None,
           'resulting_resource_version': None, 'audit_id': None}
    try:
        status, headers, body = client.patch_configmap(obj, {key: value})
        rec['t_resp_mono_ns'] = time.monotonic_ns()
        rec['http_status'] = status
        rec['audit_id'] = headers.get('Audit-Id')
        if isinstance(body, dict):
            rec['resulting_resource_version'] = body.get('metadata', {}).get('resourceVersion')
    except Exception as exc:   # never propagate into the responder path
        rec['t_resp_mono_ns'] = time.monotonic_ns()
        rec['error'] = repr(exc)
    return rec


# --------------------------------------------------------------------------- thread version
class PostGateWriter:
    def __init__(self, client, obj, key, value, delta_ms):
        """client: a KubeClient bound to the WRITER identity/connection.
        obj: ConfigMap name to PATCH; key/value: data field and new string value.
        delta_ms: float release offset (may be negative)."""
        self.client = client
        self.obj = obj
        self.key = key
        self.value = str(value)
        self.delta_ms = float(delta_ms)
        self.delta_ns = int(round(self.delta_ms * 1e6))
        self._anchor_ns = None
        self._anchor_lock = threading.Lock()
        self._armed_evt = threading.Event()
        self._abort = False
        self.thread = None
        self.result = None
        self.negative = self.delta_ms < 0

    def prearm(self):
        self.client.connect()
        self.thread = threading.Thread(target=self._run, name='e38v2-writer', daemon=True)
        self.thread.start()
        return self

    def arm(self, anchor_ns):
        with self._anchor_lock:
            if self._anchor_ns is None:
                self._anchor_ns = int(anchor_ns)
        self._armed_evt.set()

    def cancel(self):
        self._abort = True
        self._armed_evt.set()

    def join(self, timeout=30):
        if self.thread:
            self.thread.join(timeout=timeout)
        return self.result

    def _run(self):
        self._armed_evt.wait()
        if self._abort:
            return
        with self._anchor_lock:
            anchor = self._anchor_ns
        target = anchor + self.delta_ns
        _wait_until(target)
        actual = time.monotonic_ns()
        self.result = _do_patch(self.client, self.obj, self.key, self.value, target, actual)


# --------------------------------------------------------------------------- process version
def _writer_main(client_factory, conn):
    """Child process: owns the writer identity's keep-alive connection for the whole run."""
    client = client_factory()
    try:
        client.connect()
    except Exception as exc:  # reported on first job
        client_err = repr(exc)
    else:
        client_err = None
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            break
        if msg[0] == 'stop':
            break
        if msg[0] == 'ping':
            conn.send(('pong', client_err))
            continue
        if msg[0] != 'job':
            conn.send(('error', 'protocol: expected job, got %r' % (msg[0],)))
            continue
        _, obj, key, value, delta_ns = msg
        try:
            client.connect()          # no-op when the keep-alive connection is open
        except Exception:
            pass
        conn.send(('ready',))
        arm = conn.recv()
        if arm[0] == 'cancel':
            conn.send(('cancelled',))
            continue
        if arm[0] != 'arm':
            conn.send(('error', 'protocol: expected arm'))
            continue
        target = int(arm[1]) + int(delta_ns)
        _wait_until(target)
        actual = time.monotonic_ns()
        conn.send(('result', _do_patch(client, obj, key, value, target, actual)))
    try:
        client.close()
    except Exception:
        pass


class WriterProcess:
    """Persistent forked writer process. Create BEFORE opening the parent's own connections."""

    def __init__(self, client_factory):
        ctx = multiprocessing.get_context('fork')
        self.conn, child = ctx.Pipe(duplex=True)
        self.proc = ctx.Process(target=_writer_main, args=(client_factory, child),
                                name='e38v2-writer-proc', daemon=True)
        self.proc.start()
        child.close()
        self.busy = False

    def ping(self, timeout=30):
        self.conn.send(('ping',))
        if not self.conn.poll(timeout):
            raise RuntimeError('writer process did not answer ping')
        tag, err = self.conn.recv()
        return err

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


class ProcessPostGateWriter:
    """Per-trial handle on the persistent WriterProcess (same interface as PostGateWriter)."""

    def __init__(self, wproc, obj, key, value, delta_ms):
        self.wproc = wproc
        self.obj = obj
        self.key = key
        self.value = str(value)
        self.delta_ms = float(delta_ms)
        self.delta_ns = int(round(self.delta_ms * 1e6))
        self.negative = self.delta_ms < 0
        self.result = None
        self._state = 'new'

    def prearm(self, timeout=30):
        if self.wproc.busy:
            raise RuntimeError('writer process busy (previous job not collected)')
        self.wproc.busy = True
        self.wproc.conn.send(('job', self.obj, self.key, self.value, self.delta_ns))
        if not self.wproc.conn.poll(timeout):
            raise RuntimeError('writer process not ready')
        msg = self.wproc.conn.recv()
        if msg[0] != 'ready':
            raise RuntimeError('writer process: %r' % (msg,))
        self._state = 'ready'
        return self

    def arm(self, anchor_ns):
        if self._state != 'ready':
            return
        self.wproc.conn.send(('arm', int(anchor_ns)))
        self._state = 'armed'

    def cancel(self):
        if self._state == 'ready':
            self.wproc.conn.send(('cancel',))
            self._state = 'cancelling'
            if self.wproc.conn.poll(30):
                self.wproc.conn.recv()
            self._state = 'done'
            self.wproc.busy = False
        elif self._state == 'armed':
            self.join(30)

    def join(self, timeout=30):
        if self._state == 'armed':
            if self.wproc.conn.poll(timeout):
                msg = self.wproc.conn.recv()
                if msg[0] == 'result':
                    self.result = msg[1]
                self._state = 'done'
                self.wproc.busy = False
            else:
                raise RuntimeError('writer result not received within %ss' % timeout)
        return self.result
