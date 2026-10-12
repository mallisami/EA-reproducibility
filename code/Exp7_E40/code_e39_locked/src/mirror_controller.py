#!/usr/bin/env python3
"""E39 A4m mirror controller: copies the eight validity ConfigMaps onto the target Pod's annotations.

Runs as a SEPARATE PROCESS with its own identity (service account "mirror" on the live cluster;
User-Agent e39-runner/mirror on the offline fake).

Mechanism
  * initial LIST of ConfigMaps -> reconcile -> WATCH from the list resourceVersion
    (allowWatchBookmarks, timeoutSeconds); every ADDED/MODIFIED event of a policy ConfigMap triggers
    GET pod -> if the annotation differs: merge-PATCH {metadata: {uid, annotations: {key: value}}}
    (uid precondition: a replaced Pod is never written with a stale uid);
  * watch end (timeout) -> re-watch from the last seen RV; watch ERROR 410 / HTTP 410 / transport
    error -> re-LIST + reconcile ("resync") then re-watch;
  * periodic resync every resync_s in a separate thread (fallback for a stalled watch).
Unchanged values are skipped (logged with skipped=true only when they came from a watch event), so
the mirror does not bump the Pod RV needlessly (which would 409 an A4 DELETE).

Log: JSONL, schema e39.mirror.v1, one line per event:
  kind=update  {source_object, source_value, source_commit_rv, event_type, via ('watch'|'resync'),
                t_event_recv_wall_ns, t_event_recv_mono_ns, annotation_key, pod_uid, pod_rv_before,
                t_patch_send_wall_ns, t_patch_send_mono_ns, http_status, mirror_commit_rv,
                mirror_commit_wall_ns, mirror_commit_mono_ns, mirror_audit_id, skipped, error}
  kind=resync | watch_start | watch_end | error | start | stop  (diagnostics)
mirror_commit_* = response receipt stamps of the mirror PATCH; the exact commit order/time comes from
the apiserver audit log (join on mirror_audit_id / mirror_commit_rv) in the offline oracle.

CLI:  python3 mirror_controller.py --api https://127.0.0.1:PORT --namespace NS --identity mirror
          --token-file F --ca-file F --log PATH [--resync-s 5] [--watch-timeout-s 30] [--duration-s N]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from e39_kube import (CM_TO_ANN, CM_VALUE_KEY, POD_NAME, POLICY_CMS, APIError, E39Client,  # noqa: E402
                      mono_ns)

SCHEMA = 'e39.mirror.v1'


class JsonlSink:
    def __init__(self, path=None):
        self.path = path
        self.records = []
        self.lock = threading.Lock()
        if path:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)

    def write(self, rec):
        line = json.dumps(rec, sort_keys=True, separators=(',', ':'))
        with self.lock:
            self.records.append(rec)
            if self.path:
                with open(self.path, 'a', encoding='utf-8') as fh:
                    fh.write(line + '\n')


def _cm_value(obj):
    name = ((obj or {}).get('metadata') or {}).get('name')
    if name not in CM_VALUE_KEY:
        return name, None
    v = ((obj or {}).get('data') or {}).get(CM_VALUE_KEY[name])
    return name, (v if isinstance(v, str) else None)


class MirrorController:
    def __init__(self, patch_client, watch_client, sink, resync_s=5.0, watch_timeout_s=30,
                 skip_unchanged=True):
        self.pc = patch_client          # GET pod + PATCH pod + LIST/GET configmaps
        self.wc = watch_client          # only used to open watch streams (dedicated connections)
        self.sink = sink
        self.resync_s = float(resync_s)
        self.watch_timeout_s = int(watch_timeout_s)
        self.skip_unchanged = skip_unchanged
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()    # serialises use of patch_client (watch + resync threads)
        self.seq = 0
        self.last_rv = None
        self._watch_conn = None
        self.threads = []
        self.stats = {'updates': 0, 'skipped': 0, 'resyncs': 0, 'watch_starts': 0, 'errors': 0}

    # ------------------------------------------------------------------ logging
    def _log(self, kind, **fields):
        self.seq += 1
        rec = {'schema': SCHEMA, 'kind': kind, 'seq': self.seq, 'identity': self.pc.identity,
               't_wall_ns': time.time_ns(), 't_mono_ns': mono_ns()}
        rec.update(fields)
        try:
            self.sink.write(rec)
        except Exception as exc:  # never let logging kill the controller
            sys.stderr.write('MIRROR_LOG_FAILED %r\n' % (exc,))
        return rec

    # ------------------------------------------------------------------ core
    def _apply(self, cm_obj, via, event_type, t_recv_wall, t_recv_mono, log_skip=True):
        name, value = _cm_value(cm_obj)
        if name not in CM_TO_ANN or value is None:
            return None
        key = CM_TO_ANN[name]
        src_rv = (cm_obj.get('metadata') or {}).get('resourceVersion')
        base = {'source_object': name, 'source_value': value, 'source_commit_rv': src_rv,
                'event_type': event_type, 'via': via, 't_event_recv_wall_ns': t_recv_wall,
                't_event_recv_mono_ns': t_recv_mono, 'annotation_key': key, 'pod_uid': None,
                'pod_rv_before': None, 't_patch_send_wall_ns': None, 't_patch_send_mono_ns': None,
                'http_status': None, 'mirror_commit_rv': None, 'mirror_commit_wall_ns': None,
                'mirror_commit_mono_ns': None, 'mirror_audit_id': None, 'skipped': False, 'error': None}
        with self.lock:
            try:
                pod, _ = self.pc.get_pod()
                if pod is None:
                    base['error'] = 'pod_missing'
                    self.stats['skipped'] += 1
                    base['skipped'] = True
                    return self._log('update', **base)
                md = pod.get('metadata') or {}
                base['pod_uid'] = md.get('uid')
                base['pod_rv_before'] = md.get('resourceVersion')
                if self.skip_unchanged and (md.get('annotations') or {}).get(key) == value:
                    base['skipped'] = True
                    self.stats['skipped'] += 1
                    return self._log('update', **base) if log_skip else None
                base['t_patch_send_wall_ns'] = time.time_ns()
                base['t_patch_send_mono_ns'] = mono_ns()
                status, h, obj = self.pc.patch_pod_annotations({key: value}, uid=md.get('uid'))
                base['mirror_commit_mono_ns'] = mono_ns()
                base['mirror_commit_wall_ns'] = time.time_ns()
                base['http_status'] = status
                base['mirror_audit_id'] = h.get('Audit-Id')
                if status == 200:
                    base['mirror_commit_rv'] = ((obj or {}).get('metadata') or {}).get('resourceVersion')
                    self.stats['updates'] += 1
                else:
                    base['error'] = 'patch_http_%s' % status
                    self.stats['errors'] += 1
            except APIError as exc:
                base['error'] = repr(exc)
                self.stats['errors'] += 1
        return self._log('update', **base)

    def resync(self, reason):
        """LIST ConfigMaps, reconcile every policy value onto the Pod. Returns the list RV."""
        t_wall, t_mono = time.time_ns(), mono_ns()
        with self.lock:
            status, _, obj = self.pc.list_configmaps()
        if status != 200:
            self._log('error', where='list', http_status=status)
            raise APIError('LIST configmaps HTTP %s' % status)
        items = (obj or {}).get('items') or []
        list_rv = ((obj or {}).get('metadata') or {}).get('resourceVersion')
        self.stats['resyncs'] += 1
        self._log('resync', reason=reason, list_rv=list_rv, n_items=len(items))
        for it in items:
            if ((it.get('metadata') or {}).get('name')) in POLICY_CMS:
                self._apply(it, 'resync', 'LIST', t_wall, t_mono, log_skip=False)
        return list_rv

    def _cm_path(self):
        return '/api/v1/namespaces/%s/configmaps' % self.wc.ns

    def watch_once(self, since_rv):
        """One watch stream. Returns ('timeout'|'gone'|'error'|'stopped', last_rv)."""
        rv = since_rv
        self.stats['watch_starts'] += 1
        self._log('watch_start', since_rv=since_rv)
        try:
            conn, resp = self.wc.open_watch(self._cm_path(), resource_version=since_rv,
                                            timeout_seconds=self.watch_timeout_s)
        except APIError as exc:
            self._log('error', where='watch_open', error=repr(exc))
            return 'error', rv
        self._watch_conn = conn
        try:
            if resp.status == 410:
                return 'gone', rv
            if resp.status != 200:
                self._log('error', where='watch_status', http_status=resp.status)
                return 'error', rv
            while not self.stop_evt.is_set():
                try:
                    line = resp.readline()
                except (OSError, ValueError, Exception) as exc:  # closed by stop() or network error
                    if self.stop_evt.is_set():
                        return 'stopped', rv
                    self._log('error', where='watch_read', error=repr(exc))
                    return 'error', rv
                if not line:
                    return 'timeout', rv
                line = line.strip()
                if not line:
                    continue
                t_wall, t_mono = time.time_ns(), mono_ns()
                try:
                    ev = json.loads(line)
                except ValueError:
                    self._log('error', where='watch_parse', line=line[:200].decode('utf-8', 'replace'))
                    return 'error', rv
                typ = ev.get('type')
                obj = ev.get('object') or {}
                if typ == 'ERROR':
                    code = obj.get('code')
                    self._log('error', where='watch_event', code=code, reason=obj.get('reason'))
                    return ('gone' if code == 410 else 'error'), rv
                new_rv = (obj.get('metadata') or {}).get('resourceVersion')
                if new_rv:
                    rv = new_rv
                    self.last_rv = rv
                if typ in ('ADDED', 'MODIFIED'):
                    self._apply(obj, 'watch', typ, t_wall, t_mono)
            return 'stopped', rv
        finally:
            self._watch_conn = None
            try:
                conn.close()
            except Exception:
                pass
            self._log('watch_end', last_rv=rv)

    def run_watch_loop(self):
        rv = None
        backoff = 0.05
        while not self.stop_evt.is_set():
            if rv is None:
                try:
                    rv = self.resync('initial' if self.stats['resyncs'] == 0 else 'relist')
                    backoff = 0.05
                except APIError as exc:
                    self._log('error', where='relist', error=repr(exc))
                    self.stop_evt.wait(backoff)
                    backoff = min(backoff * 2, 2.0)
                    continue
            result, rv2 = self.watch_once(rv)
            if result == 'timeout':
                rv = rv2
            elif result in ('gone', 'error'):
                rv = None
                self.stop_evt.wait(0.02)
            else:
                break

    def run_resync_loop(self):
        while not self.stop_evt.wait(self.resync_s):
            try:
                self.resync('periodic')
            except APIError as exc:
                self._log('error', where='periodic_resync', error=repr(exc))

    def start(self):
        self._log('start', resync_s=self.resync_s, watch_timeout_s=self.watch_timeout_s,
                  namespace=self.pc.ns, pod_name=self.pc.pod_name)
        for target in (self.run_watch_loop, self.run_resync_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)
        return self

    def stop(self, join_timeout=5.0):
        self.stop_evt.set()
        conn = self._watch_conn
        if conn is not None and getattr(conn, 'sock', None) is not None:
            try:
                import socket as _s
                conn.sock.shutdown(_s.SHUT_RDWR)
            except OSError:
                pass
        for t in self.threads:
            t.join(join_timeout)
        self._log('stop', stats=dict(self.stats))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--api', required=True, help='http(s)://127.0.0.1:PORT (loopback only)')
    ap.add_argument('--namespace', required=True)
    ap.add_argument('--identity', default='mirror')
    ap.add_argument('--token-file')
    ap.add_argument('--ca-file')
    ap.add_argument('--pod-name', default=POD_NAME)
    ap.add_argument('--log', required=True)
    ap.add_argument('--resync-s', type=float, default=5.0)
    ap.add_argument('--watch-timeout-s', type=int, default=30)
    ap.add_argument('--duration-s', type=float, default=0.0, help='0 = until SIGTERM/SIGINT')
    a = ap.parse_args(argv)
    mk = lambda: E39Client(a.api, a.namespace, a.identity, token_path=a.token_file, ca_path=a.ca_file,  # noqa: E731
                           pod_name=a.pod_name)
    mc = MirrorController(mk(), mk(), JsonlSink(a.log), resync_s=a.resync_s, watch_timeout_s=a.watch_timeout_s)
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    mc.start()
    sys.stdout.write('MIRROR_READY\n')
    sys.stdout.flush()
    end = time.monotonic() + a.duration_s if a.duration_s > 0 else None
    while not done.wait(0.2):
        if end is not None and time.monotonic() >= end:
            break
    mc.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
