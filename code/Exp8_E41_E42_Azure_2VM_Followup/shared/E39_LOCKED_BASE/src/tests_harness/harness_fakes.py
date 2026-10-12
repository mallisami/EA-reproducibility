"""127.0.0.1-only fake upstream for the E39 harness tests (NOT part of any live run).

FakeKube: minimal ConfigMap API (GET / LIST / POST / PATCH / DELETE) for any namespace,
global monotonically increasing resourceVersion, uuid Audit-Id on every response,
records every request (method, path, headers, body, connection id), counts accepted
connections, optional TLS, and a chunked streaming endpoint (/stream-test).
"""
from __future__ import annotations
import json, os, shutil, subprocess, threading, time, uuid, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class _H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True     # headers and body are separate writes (avoid 40 ms delayed-ACK stalls)

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        with self.server.fk.lock:
            self.server.fk.connections += 1
            self.conn_id = self.server.fk.connections

    def _send(self, code, obj, aid=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Audit-Id', aid or uuid.uuid4().hex)
        self.send_header('X-Fake-Conn', str(self.conn_id))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        fk = self.server.fk
        n = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(n) if n else b''
        aid = uuid.uuid4().hex
        u = urlsplit(self.path)
        parts = u.path.strip('/').split('/')
        rec = {'method': self.command, 'path': self.path, 'headers': dict(self.headers.items()),
               'body': raw, 'conn_id': self.conn_id, 'audit_id': aid, 't_mono_ns': time.monotonic_ns()}
        if fk.latency_s:
            time.sleep(fk.latency_s)
        if u.path == '/stream-test':
            with fk.lock:
                fk.requests.append(rec)
            self.send_response(200)
            self.send_header('Audit-Id', aid)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for i in range(3):
                c = ('{"event":%d}\n' % i).encode()
                self.wfile.write(b'%x\r\n%s\r\n' % (len(c), c))
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b'0\r\n\r\n')
            return
        # /api/v1/namespaces/<ns>/configmaps[/<name>]
        if len(parts) < 5 or parts[:3] != ['api', 'v1', 'namespaces'] or parts[4] != 'configmaps':
            with fk.lock:
                fk.requests.append(rec)
            return self._send(404, {'kind': 'Status', 'code': 404}, aid)
        ns = parts[3]
        name = parts[5] if len(parts) > 5 else None
        body = json.loads(raw) if raw else None
        with fk.lock:
            fk.requests.append(rec)
            if self.command == 'GET' and name is None:
                items = [fk.obj(ns, k[1]) for k in sorted(fk.cms) if k[0] == ns]
                return self._send(200, {'kind': 'ConfigMapList', 'items': items,
                                        'metadata': {'resourceVersion': str(fk.rev)}}, aid)
            if self.command == 'GET':
                if (ns, name) not in fk.cms:
                    return self._send(404, {'kind': 'Status', 'code': 404}, aid)
                return self._send(200, fk.obj(ns, name), aid)
            if self.command == 'POST' and name is None:
                nm = body['metadata']['name']
                if (ns, nm) in fk.cms:
                    return self._send(409, {'kind': 'Status', 'code': 409}, aid)
                fk.put(ns, nm, dict(body.get('data') or {}))
                return self._send(201, fk.obj(ns, nm), aid)
            if self.command == 'PATCH' and name is not None:
                if (ns, name) not in fk.cms:
                    return self._send(404, {'kind': 'Status', 'code': 404}, aid)
                data = dict(fk.cms[(ns, name)]['data'])
                data.update({k: str(v) for k, v in ((body or {}).get('data') or {}).items()})
                fk.put(ns, name, data)
                o = fk.obj(ns, name)
                fk.commits.append({'audit_id': aid, 'name': name, 'rv': o['metadata']['resourceVersion'],
                                   'data': data, 't_mono_ns': time.monotonic_ns()})
                return self._send(200, o, aid)
            if self.command == 'DELETE' and name is not None:
                fk.cms.pop((ns, name), None)
                fk.rev += 1
                return self._send(200, {'kind': 'Status', 'status': 'Success'}, aid)
        return self._send(405, {'kind': 'Status', 'code': 405}, aid)

    do_GET = do_POST = do_PATCH = do_DELETE = _handle


class FakeKube:
    def __init__(self, tls_cert=None, tls_key=None, latency_s=0.0):
        self.lock = threading.Lock()
        self.cms = {}
        self.rev = 1000
        self.requests = []
        self.commits = []
        self.connections = 0
        self.latency_s = latency_s
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), _H)
        self.httpd.daemon_threads = True
        self.httpd.fk = self
        self.tls = bool(tls_cert)
        if self.tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(tls_cert, tls_key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = None

    @property
    def base_url(self):
        return '%s://127.0.0.1:%d' % ('https' if self.tls else 'http', self.port)

    def put(self, ns, name, data):
        self.rev += 1
        self.cms[(ns, name)] = {'data': dict(data), 'rv': str(self.rev)}

    def seed(self, ns, name, data):
        with self.lock:
            self.put(ns, name, data)

    def obj(self, ns, name):
        c = self.cms[(ns, name)]
        return {'apiVersion': 'v1', 'kind': 'ConfigMap',
                'metadata': {'name': name, 'namespace': ns, 'resourceVersion': c['rv']}, 'data': dict(c['data'])}

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def make_cert(dirpath, cn='127.0.0.1'):
    """Self-signed cert with IP SAN 127.0.0.1 (via openssl CLI); returns (cert, key) or None."""
    exe = shutil.which('openssl')
    if not exe:
        return None
    cert, key = os.path.join(dirpath, cn + '-cert.pem'), os.path.join(dirpath, cn + '-key.pem')
    subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
                    '-days', '1', '-subj', '/CN=' + cn, '-addext', 'subjectAltName=IP:127.0.0.1'],
                   check=True, capture_output=True)
    return cert, key
