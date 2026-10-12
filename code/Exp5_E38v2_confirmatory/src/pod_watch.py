#!/usr/bin/env python3
"""E38v2 RERUN pod-event recorder (read-only; separate process, never inside the runner).

Why (found in the rerun's pre-lock exploration, see DEVIATIONS): with a genuinely Running target
Pod, a DELETE with gracePeriodSeconds=0 first commits deletionTimestamp; the kubelet then kills the
container and its status update can remove the object ("Going to delete object from registry,
triggered by update") while the responder's DELETE is still in its storage-delete phase (this is
frequent in the webhook arm, whose second admission call takes ~10-20 ms). The responder's DELETE is
then answered 200 with an object WITHOUT resourceVersion, so neither the audit responseObject nor
the client response carries the deletion revision. This recorder watches all Pods in the test
namespace and logs every event with its resourceVersion; the watch DELETED event carries the
deletion revision, and the MODIFIED event that first shows deletionTimestamp carries the revision at
which the deletion was accepted. analysis/oracle.py (plan v1.2) uses the DELETED revision ONLY as a
fallback when both pre-registered deletion-RV sources are missing, and checks it against them
whenever they exist.

The watch is continued by resourceVersion (no gaps). If the apiserver answers 410 Gone, the
recorder relists and writes a GAP marker (the oracle never uses events across an unresolved gap for
a Pod whose DELETED event is missing: it simply finds no DELETED event -> UNKNOWN).

Credentials: the reset identity's token (Role e38v2-reset: pods get/list/watch in the test
namespace); only GET requests are issued. Loopback https://127.0.0.1:<port> with pinned CA only.

Output: JSON lines, schema e38v2.podevent.v1:
  {schema, seq, type, t_recv_wall_ns, t_recv_mono_ns, uid, name, rv, deletion_timestamp,
   deletion_grace_period_seconds, phase, ready, container_state, node, webhook_label}
  type in LIST (initial / re-list item), ADDED, MODIFIED, DELETED, BOOKMARK, GAP, START, STOP, ERROR
Usage: pod_watch.py --api-server https://127.0.0.1:PORT --ca-file CA --token-file TOKEN
                    --namespace e38v2-<run_id> --out pod_events.jsonl [--stop-file PATH]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import ssl
import sys
import time
from urllib.parse import quote, urlsplit

SCHEMA = 'e38v2.podevent.v1'
WEBHOOK_LABEL = ('e38v2-webhook', 'enforce')


def summarize(obj):
    md = obj.get('metadata') or {}
    st = obj.get('status') or {}
    ready = None
    for c in st.get('conditions') or []:
        if c.get('type') == 'Ready':
            ready = c.get('status') == 'True'
    cstate = None
    for cs in st.get('containerStatuses') or []:
        s = cs.get('state') or {}
        cstate = 'running' if 'running' in s else ('terminated' if 'terminated' in s else
                                                   ('waiting' if 'waiting' in s else None))
    return {'uid': md.get('uid'), 'name': md.get('name'), 'rv': md.get('resourceVersion'),
            'deletion_timestamp': md.get('deletionTimestamp'),
            'deletion_grace_period_seconds': md.get('deletionGracePeriodSeconds'),
            'phase': st.get('phase'), 'ready': ready, 'container_state': cstate,
            'node': (obj.get('spec') or {}).get('nodeName'),
            'webhook_label': (md.get('labels') or {}).get(WEBHOOK_LABEL[0]) == WEBHOOK_LABEL[1]}


class Recorder:
    def __init__(self, out_path):
        self.fh = open(out_path, 'a', encoding='utf-8')
        self.seq = 0

    def write(self, typ, obj=None, **extra):
        self.seq += 1
        rec = {'schema': SCHEMA, 'seq': self.seq, 'type': typ,
               't_recv_wall_ns': time.time_ns(), 't_recv_mono_ns': time.monotonic_ns()}
        if obj is not None:
            rec.update(summarize(obj))
        rec.update(extra)
        self.fh.write(json.dumps(rec, sort_keys=True, separators=(',', ':')) + '\n')
        self.fh.flush()

    def close(self):
        self.fh.close()


class Watcher:
    def __init__(self, api_server, ca_file, token_file, namespace, rec, stop_file=None):
        u = urlsplit(api_server)
        if u.scheme != 'https' or u.hostname != '127.0.0.1' or not u.port:
            raise SystemExit('only https://127.0.0.1:<port> is supported')
        if not namespace.startswith('e38v2-') or namespace == 'e38v2-system':
            raise SystemExit('namespace must be an e38v2-<run_id> test namespace')
        self.host, self.port = u.hostname, u.port
        self.ctx = ssl.create_default_context(cafile=ca_file)
        with open(token_file, encoding='utf-8') as fh:
            self.token = fh.read().strip()
        self.ns = namespace
        self.rec = rec
        self.stop_file = stop_file
        self.stopping = False
        self.rv = None

    def _conn(self, timeout):
        return http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.ctx)

    def _headers(self):
        return {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json',
                'User-Agent': 'e38v2-podwatch/1'}

    def should_stop(self):
        return self.stopping or (self.stop_file is not None and os.path.exists(self.stop_file))

    def list_pods(self):
        c = self._conn(30)
        try:
            c.request('GET', '/api/v1/namespaces/%s/pods' % quote(self.ns, safe=''), headers=self._headers())
            r = c.getresponse()
            body = r.read()
            if r.status != 200:
                raise RuntimeError('list HTTP %d: %s' % (r.status, body[:200]))
            doc = json.loads(body)
        finally:
            c.close()
        for item in doc.get('items') or []:
            self.rec.write('LIST', item)
        self.rv = (doc.get('metadata') or {}).get('resourceVersion')
        return self.rv

    def watch_once(self, timeout_s=60):
        """One watch request from self.rv; returns 'ok' (server closed normally), 'gone' (410)."""
        path = ('/api/v1/namespaces/%s/pods?watch=1&allowWatchBookmarks=true&timeoutSeconds=%d&resourceVersion=%s'
                % (quote(self.ns, safe=''), timeout_s, quote(str(self.rv), safe='')))
        c = self._conn(timeout_s + 30)
        try:
            c.request('GET', path, headers=self._headers())
            r = c.getresponse()
            if r.status == 410:
                r.read()
                return 'gone'
            if r.status != 200:
                raise RuntimeError('watch HTTP %d: %s' % (r.status, r.read()[:200]))
            while not self.should_stop():
                line = r.readline()
                if not line:
                    return 'ok'
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                typ, obj = ev.get('type'), ev.get('object') or {}
                if typ == 'ERROR':
                    code = obj.get('code')
                    self.rec.write('ERROR', None, code=code, message=str(obj.get('message'))[:300])
                    if code == 410:
                        return 'gone'
                    return 'ok'
                if typ == 'BOOKMARK':
                    self.rv = (obj.get('metadata') or {}).get('resourceVersion') or self.rv
                    self.rec.write('BOOKMARK', None, rv=self.rv)
                    continue
                self.rec.write(typ, obj)
                self.rv = (obj.get('metadata') or {}).get('resourceVersion') or self.rv
            return 'ok'
        finally:
            c.close()

    def run(self):
        self.rec.write('START', None, namespace=self.ns, pid=os.getpid())
        self.list_pods()
        while not self.should_stop():
            try:
                res = self.watch_once()
            except (OSError, ssl.SSLError, http.client.HTTPException, RuntimeError, ValueError) as exc:
                self.rec.write('ERROR', None, message=repr(exc)[:300], rv=self.rv)
                time.sleep(0.5)
                continue
            if res == 'gone':
                self.rec.write('GAP', None, rv=self.rv, reason='410 Gone; relisting')
                try:
                    self.list_pods()
                except Exception as exc:  # noqa: BLE001
                    self.rec.write('ERROR', None, message=repr(exc)[:300])
                    time.sleep(1.0)
        self.rec.write('STOP', None, rv=self.rv)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--api-server', required=True)
    ap.add_argument('--ca-file', required=True)
    ap.add_argument('--token-file', required=True)
    ap.add_argument('--namespace', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--stop-file')
    a = ap.parse_args(argv)
    rec = Recorder(a.out)
    w = Watcher(a.api_server, a.ca_file, a.token_file, a.namespace, rec, a.stop_file)

    def _term(signum, frame):
        w.stopping = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        w.run()
    finally:
        rec.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
