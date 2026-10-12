#!/usr/bin/env python3
"""E39 measurement-harness shared helpers (loopback-only HTTP client, precise sleep).

LoopbackClient is a small persistent keep-alive client with the SAME connection
semantics as e38v2_runner.KubeClient (http://127.0.0.1:PORT for offline fakes,
https://127.0.0.1:PORT with a pinned CA + bearer token for the live kind cluster),
but with a caller-supplied write allowlist so that separate harness identities
(load generator in namespace e40-load, delay-proxy calibration, ...) do not need
the runner's e40-specific safety rail. It never talks to anything but 127.0.0.1.

Interface (compatible with KubeClient where it matters):
    connect(), close(), request(method, path, body=None, extra_headers=None)
        -> (status:int, headers:CIHeaders, obj:dict|None)
    get_configmap(name), patch_configmap(name, data)
"""
from __future__ import annotations
import http.client, json, ssl, time
from urllib.parse import urlsplit

SPIN_WINDOW_NS = 1_000_000


def mono_ns():
    return time.monotonic_ns()


def sleep_until_ns(target_ns, spin_ns=SPIN_WINDOW_NS):
    """Coarse sleep while far away, busy-spin for the final ``spin_ns``."""
    while True:
        remaining = target_ns - time.monotonic_ns()
        if remaining <= 0:
            return
        if remaining > spin_ns:
            time.sleep((remaining - spin_ns) / 1e9)


def check_loopback_url(base):
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname != '127.0.0.1' or parsed.port is None
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or parsed.netloc != f'127.0.0.1:{parsed.port}'):
        raise ValueError('Only an exact http(s)://127.0.0.1:PORT loopback URL is supported: %r' % (base,))
    return parsed


class CIHeaders(dict):
    """Case-insensitive header dict (keys stored lower-case)."""

    def __init__(self, pairs):
        super().__init__()
        for k, v in pairs:
            self[k.lower()] = v

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class HarnessAPIError(RuntimeError):
    pass


def allow_reads_only(method, path, body):
    return method in ('GET', 'HEAD')


class LoopbackClient:
    """Persistent keep-alive loopback client for one harness identity.

    allow(method, path, body) -> bool is the write allowlist (safety rail); GETs are
    subject to it too so that an identity can be scoped to one namespace.
    """

    def __init__(self, base, namespace, identity, token_path=None, ca_path=None, token=None,
                 allow=allow_reads_only, timeout_s=20.0, user_agent_prefix='e40-harness/'):
        parsed = check_loopback_url(base)
        self.scheme = parsed.scheme
        self.host, self.port = parsed.hostname, parsed.port
        self.base = base.rstrip('/')
        self.ns = namespace
        self.identity = identity
        self.allow = allow
        self.timeout_s = timeout_s
        self.user_agent = user_agent_prefix + identity
        self.conn = None
        self.connections_opened = 0
        self._token = token
        self._ssl = None
        if self.scheme == 'https':
            if not ca_path:
                raise ValueError('https requires a pinned CA file')
            ctx = ssl.create_default_context(cafile=ca_path)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            self._ssl = ctx
        if token_path:
            with open(token_path, encoding='utf-8') as fh:
                self._token = fh.read().strip()

    def _ns_root(self):
        return f'/api/v1/namespaces/{self.ns}'

    def connect(self):
        if self.conn is None:
            if self.scheme == 'https':
                self.conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout_s,
                                                        context=self._ssl)
            else:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
            self.conn.connect()
            self.connections_opened += 1
        return self

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def request(self, method, path, body=None, extra_headers=None):
        if not self.allow(method, path, body):
            raise HarnessAPIError(f'BLOCKED by harness allowlist: {method} {path} ({self.identity})')
        payload = json.dumps(body, sort_keys=True, separators=(',', ':')).encode() if body is not None else None
        headers = {'Content-Type': 'application/merge-patch+json' if method == 'PATCH' else 'application/json',
                   'Accept': 'application/json', 'User-Agent': self.user_agent}
        if self._token:
            headers['Authorization'] = 'Bearer ' + self._token
        if payload is not None:
            headers['Content-Length'] = str(len(payload))
        if extra_headers:
            headers.update(extra_headers)
        for attempt in (0, 1):
            reused = self.conn is not None
            self.connect()
            try:
                self.conn.request(method, path, body=payload, headers=headers)
                resp = self.conn.getresponse()
                raw = resp.read()
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.CannotSendRequest) as exc:
                self.close()
                if attempt == 0 and reused:
                    continue
                raise HarnessAPIError(f'{method} {path}: {exc!r}') from exc
            except (http.client.HTTPException, OSError) as exc:
                self.close()
                raise HarnessAPIError(f'{method} {path}: {exc!r}') from exc
            status = resp.status
            rheaders = CIHeaders(resp.getheaders())
            if resp.will_close:
                self.close()
            try:
                obj = json.loads(raw) if raw else None
            except ValueError:
                obj = None
            return status, rheaders, obj
        raise HarnessAPIError(f'{method} {path}: unreachable')

    def get_configmap(self, name):
        return self.request('GET', f'{self._ns_root()}/configmaps/{name}')

    def patch_configmap(self, name, data):
        body = {'data': {k: str(v) for k, v in data.items()}}
        return self.request('PATCH', f'{self._ns_root()}/configmaps/{name}', body)
