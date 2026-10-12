#!/usr/bin/env python3
"""E40 wrapper for the (prefix-renamed) E38v2/E39 pod-event recorder src/pod_watch.py.

Only the endpoint rail differs: pod_watch.Watcher accepts https://127.0.0.1:<port> only; inside the AKS runner Pod the
endpoint is https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT (exact match enforced here). Recorder, watch loop,
relist/GAP handling and output schema (e38v2.podevent.v1) are the unchanged pod_watch code.
Usage: e40_podwatch.py --namespace e40-<run_id> --out pod_events.jsonl [--stop-file PATH]   (reset identity token)
"""
from __future__ import annotations

import argparse
import os
import signal
import ssl
import sys
from pathlib import Path

ROOT = Path(os.environ.get('E40_ROOT', '/e40'))
sys.path.insert(0, str(ROOT / 'src'))
import pod_watch as PW  # noqa: E402

HOST = (os.environ.get('KUBERNETES_SERVICE_HOST') or '').strip()
PORT = int((os.environ.get('KUBERNETES_SERVICE_PORT') or '443').strip())


class InClusterWatcher(PW.Watcher):
    def __init__(self, ca_file, token_file, namespace, rec, stop_file=None):
        if not HOST:
            raise SystemExit('not in cluster')
        if not namespace.startswith('e40-') or namespace in ('e40-system', 'e40-load'):
            raise SystemExit('namespace must be an e40-<run_id> test namespace')
        self.host, self.port = HOST, PORT
        self.ctx = ssl.create_default_context(cafile=ca_file)
        with open(token_file, encoding='utf-8') as fh:
            self.token = fh.read().strip()
        self.ns = namespace
        self.rec = rec
        self.stop_file = stop_file
        self.stopping = False
        self.rv = None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--namespace', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--stop-file')
    ap.add_argument('--ca-file', default='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
    ap.add_argument('--token-file', default='/tokens/reset.token')
    a = ap.parse_args(argv)
    rec = PW.Recorder(a.out)
    w = InClusterWatcher(a.ca_file, a.token_file, a.namespace, rec, a.stop_file)

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
