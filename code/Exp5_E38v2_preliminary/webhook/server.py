#!/usr/bin/env python3
"""E38v2 validating admission webhook (server-side DELETE re-check).

Handles AdmissionReview v1 for pods DELETE in ONE configured test namespace.
At admission time it re-reads the eight evidence/scope/policy ConfigMaps from
kube-apiserver with quorum GETs (plain GET by name, no resourceVersion
parameter -> served from etcd, never from the watch cache or an informer) and
denies the DELETE unless policy.evaluate() says V_core holds.

Fail closed for the target: any API error, 404, timeout, deadline overrun,
unparseable value or internal exception -> deny.
Defensive for everything else: other namespaces, other operations/resources,
exempt identities (reset SA, kubelets, namespace/GC controllers) and pods that
are already terminating are allowed without evaluation (the
ValidatingWebhookConfiguration also scopes these out).

Python stdlib only. Configuration via environment:
  WEBHOOK_NAMESPACE      required, e.g. e38v2-r20260926a (must start "e38v2-", not e38v2-system)
  WEBHOOK_BIND           default 127.0.0.1 (the Deployment sets 0.0.0.0 inside the Pod)
  WEBHOOK_PORT           default 8443
  WEBHOOK_TLS_CERT/KEY   default /tls/tls.crt, /tls/tls.key
  WEBHOOK_LOG_PATH       default /var/log/e38v2-webhook/webhook_log.jsonl
  WEBHOOK_API_TIMEOUT_S  per-GET socket timeout, default 1.0
  WEBHOOK_BUDGET_S       total evaluation budget, default 3.5 (< timeoutSeconds 5)
  WEBHOOK_EXEMPT_USERS   comma-separated usernames allowed without evaluation
  KUBERNETES_SERVICE_HOST / KUBERNETES_SERVICE_PORT   in-cluster API endpoint
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import policy  # noqa: E402

SCHEMA = "e38v2.webhook.v1"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
NAMESPACE_RE = re.compile(r"^e38v2-[a-z0-9]([-a-z0-9]{0,54}[a-z0-9])?$")
MAX_BODY = 4 * 1024 * 1024
# Usernames always exempt (final grace-0 DELETE from kubelet, namespace teardown, GC).
BUILTIN_EXEMPT_USERS = (
    "system:serviceaccount:kube-system:namespace-controller",
    "system:serviceaccount:kube-system:generic-garbage-collector",
)
BUILTIN_EXEMPT_PREFIXES = ("system:node:",)


class ApiError(Exception):
    """Any failure to obtain a ConfigMap. Always leads to deny."""


# --------------------------------------------------------------------------- API client
class KubeClient:
    """Minimal kube-apiserver client: one persistent keep-alive HTTPS connection.

    A lock serialises use of the connection; one admission request holds it for
    all eight reads, so concurrent admissions do not interleave reads.
    """

    def __init__(self, host: str, port: int, token_path: str, ca_path: Optional[str],
                 timeout_s: float = 1.0, ssl_context: Optional[ssl.SSLContext] = None):
        self.host = host
        self.port = int(port)
        self.token_path = token_path
        self.timeout_s = float(timeout_s)
        if ssl_context is None:
            ssl_context = ssl.create_default_context(cafile=ca_path)
        self.ctx = ssl_context
        self._conn: Optional[http.client.HTTPSConnection] = None
        self._conn_uses = 0
        self.lock = threading.Lock()
        self._token = None
        self._token_read = 0.0
        self.connections_opened = 0

    @classmethod
    def in_cluster(cls, timeout_s: float) -> "KubeClient":
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if not host:
            raise SystemExit("KUBERNETES_SERVICE_HOST not set: not running in-cluster")
        return cls(host, int(port), SA_DIR + "/token", SA_DIR + "/ca.crt", timeout_s)

    def _token_value(self) -> str:
        # Bound SA tokens are rotated by the kubelet; re-read the file every 30 s.
        now = time.monotonic()
        if self._token is None or now - self._token_read > 30:
            with open(self.token_path, "r", encoding="utf-8") as fh:
                self._token = fh.read().strip()
            self._token_read = now
        return self._token

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._conn_uses = 0

    def _connection(self, timeout: float) -> http.client.HTTPSConnection:
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.ctx)
            self._conn_uses = 0
            self.connections_opened += 1
        else:
            self._conn.timeout = timeout
            if self._conn.sock is not None:
                self._conn.sock.settimeout(timeout)
        return self._conn

    def _get_once(self, path: str, timeout: float):
        conn = self._connection(timeout)
        reused = self._conn_uses > 0
        headers = {"Authorization": "Bearer " + self._token_value(), "Accept": "application/json",
                   "User-Agent": "e38v2-webhook/1"}
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                http.client.CannotSendRequest, http.client.BadStatusLine) as exc:
            self._close()
            if reused:
                return None, exc  # stale keep-alive connection: caller may retry once
            raise ApiError("connection: %r" % (exc,))
        except (socket.timeout, TimeoutError) as exc:
            self._close()
            raise ApiError("timeout: %r" % (exc,))
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            self._close()
            raise ApiError("transport: %r" % (exc,))
        self._conn_uses += 1
        if resp.will_close:
            self._close()
        return (resp.status, body), None

    def get_configmap(self, namespace: str, name: str, deadline: float) -> Dict[str, Any]:
        """Quorum GET of one ConfigMap. Caller must hold self.lock."""
        # No query string at all: no resourceVersion -> consistent read from etcd.
        path = "/api/v1/namespaces/%s/configmaps/%s" % (quote(namespace, safe=""), quote(name, safe=""))
        for attempt in (0, 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiError("deadline exceeded before GET %s" % name)
            result, stale = self._get_once(path, min(self.timeout_s, remaining))
            if result is None:
                if attempt == 0:
                    continue
                raise ApiError("connection: %r" % (stale,))
            status, body = result
            if status != 200:
                raise ApiError("http_%d GET %s" % (status, name))
            try:
                obj = json.loads(body)
            except ValueError:
                raise ApiError("non-JSON body GET %s" % name)
            if not isinstance(obj, dict) or obj.get("kind") != "ConfigMap":
                raise ApiError("unexpected object GET %s" % name)
            return obj
        raise ApiError("unreachable")


# --------------------------------------------------------------------------- log writer
class JsonlLog:
    def __init__(self, path: Optional[str], echo_stdout: bool = True):
        self.path = path
        self.echo = echo_stdout
        self.lock = threading.Lock()
        if path:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self.lock:
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                        fh.flush()
                except OSError as exc:  # stdout copy below remains as backup record
                    sys.stderr.write("LOG_WRITE_FAILED %r\n" % (exc,))
            if self.echo:
                sys.stdout.write("WEBHOOK_LOG " + line + "\n")
                sys.stdout.flush()


# --------------------------------------------------------------------------- admission logic
class BadReview(Exception):
    pass


class Admission:
    def __init__(self, namespace: str, client: Any, log: Any, exempt_users: Sequence[str] = (),
                 budget_s: float = 3.5, wall_ns: Callable[[], int] = time.time_ns):
        if not NAMESPACE_RE.match(namespace or "") or namespace == "e38v2-system":
            raise ValueError("WEBHOOK_NAMESPACE must be an e38v2-<run_id> test namespace")
        self.namespace = namespace
        self.client = client
        self.log = log
        self.exempt = set(BUILTIN_EXEMPT_USERS) | {u for u in exempt_users if u}
        self.budget_s = float(budget_s)
        self.wall_ns = wall_ns

    @staticmethod
    def _response(uid: str, allowed: bool, message: str) -> Dict[str, Any]:
        resp: Dict[str, Any] = {"uid": uid, "allowed": bool(allowed)}
        if allowed:
            resp["status"] = {"code": 200, "message": message[:1024]}
        else:
            resp["status"] = {"code": 403, "reason": "Forbidden",
                              "message": ("e38v2-webhook: " + message)[:1024]}
        return {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": resp}

    def _exempt(self, username: str) -> bool:
        return username in self.exempt or any(username.startswith(p) for p in BUILTIN_EXEMPT_PREFIXES)

    def review(self, review: Any) -> Dict[str, Any]:
        received = self.wall_ns()
        t0 = time.monotonic()
        if not isinstance(review, dict) or review.get("apiVersion") != "admission.k8s.io/v1" \
                or review.get("kind") != "AdmissionReview" or not isinstance(review.get("request"), dict):
            raise BadReview("not an admission.k8s.io/v1 AdmissionReview")
        req = review["request"]
        uid = req.get("uid")
        if not isinstance(uid, str) or not uid:
            raise BadReview("request.uid missing")

        # ---- defensive scoping: anything that is not our target is allowed untouched
        if req.get("namespace") != self.namespace:
            return self._response(uid, True, "not target namespace")
        res = req.get("resource") or {}
        if req.get("operation") != "DELETE" or res.get("group", "") != "" or res.get("resource") != "pods" \
                or req.get("subResource"):
            return self._response(uid, True, "not pods DELETE")
        username = ((req.get("userInfo") or {}).get("username")) or ""
        if self._exempt(username):
            return self._response(uid, True, "exempt identity")
        old = req.get("oldObject") if isinstance(req.get("oldObject"), dict) else {}
        meta = old.get("metadata") if isinstance(old.get("metadata"), dict) else {}
        pod_rv = meta.get("resourceVersion") if isinstance(meta.get("resourceVersion"), str) else ""
        if meta.get("deletionTimestamp"):
            # E38v2 integration: a Pod DELETE is admitted in two storage phases (set deletionTimestamp,
            # then remove); the second-phase call on the terminating object is allowed WITHOUT
            # evaluation, but is logged so every invocation per DELETE is visible to the oracle.
            self._log({"schema": SCHEMA, "request_uid": uid,
                       "pod_name": req.get("name") if isinstance(req.get("name"), str) else "",
                       "pod_uid": meta.get("uid") if isinstance(meta.get("uid"), str) else "",
                       "pod_resource_version": pod_rv, "pod_terminating": True,
                       "received_wall_ns": received, "reads": [], "decision": "allow",
                       "reason": "allow: pod already terminating (not evaluated)",
                       "completed_wall_ns": self.wall_ns()})
            return self._response(uid, True, "pod already terminating")

        # ---- target: evaluate with fresh quorum reads, fail closed
        reads: List[Dict[str, str]] = []
        values: Dict[str, Any] = {}
        try:
            deadline = t0 + self.budget_s
            with self.client.lock:
                for name in policy.CONFIGMAPS:
                    # per-read wall-clock stamps (E38v2 integration): the oracle's time rule for a
                    # webhook denial uses the send time of the read of the written object.
                    t_send = self.wall_ns()
                    obj = self.client.get_configmap(self.namespace, name, deadline)
                    t_recv = self.wall_ns()
                    value = policy.extract_value(name, obj)
                    rv = (obj.get("metadata") or {}).get("resourceVersion")
                    reads.append({"object": name, "resource_version": rv if isinstance(rv, str) else "",
                                  "value": value if value is not None else "<missing>",
                                  "t_send_wall_ns": t_send, "t_recv_wall_ns": t_recv})
                    values[name] = value
            if time.monotonic() > deadline:
                allowed, reason = False, "deny: deadline exceeded after reads"
            else:
                allowed, reason = policy.evaluate(values)
        except ApiError as exc:
            allowed, reason = False, "deny: api_error %s" % (exc,)
        except Exception as exc:  # fail closed on any bug
            allowed, reason = False, "deny: internal_error %r" % (exc,)

        record = {
            "schema": SCHEMA,
            "request_uid": uid,
            "pod_name": req.get("name") if isinstance(req.get("name"), str) else "",
            "pod_uid": meta.get("uid") if isinstance(meta.get("uid"), str) else "",
            "pod_resource_version": pod_rv,
            "pod_terminating": False,
            "received_wall_ns": received,
            "reads": reads,
            "decision": "allow" if allowed else "deny",
            "reason": reason,
            "completed_wall_ns": self.wall_ns(),
        }
        self._log(record)
        return self._response(uid, allowed, reason)

    def _log(self, record: Dict[str, Any]) -> None:
        try:
            self.log.write(record)
        except Exception as exc:
            sys.stderr.write("LOG_WRITE_FAILED %r\n" % (exc,))


# --------------------------------------------------------------------------- HTTPS server
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60  # idle keep-alive timeout for connections from kube-apiserver
    server_version = "e38v2-webhook/1"
    # E38v2 integration fix: buffer the response so status line, headers and body leave in ONE
    # write (flushed by handle_one_request). With the default unbuffered writer the headers and the
    # body were separate TLS records/segments and a Nagle / delayed-ACK interaction stalled allow
    # responses by ~40 ms in the live smoke run.
    wbufsize = 1 << 16

    def log_message(self, fmt, *args):  # keep stdout for WEBHOOK_LOG lines
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/validate":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            self._send(400, b'{"error":"bad content-length"}')
            return
        raw = self.rfile.read(length)
        try:
            review = json.loads(raw)
            out = self.server.admission.review(review)
        except (ValueError, BadReview) as exc:
            # HTTP error -> apiserver applies failurePolicy=Fail -> request rejected.
            self._send(400, json.dumps({"error": str(exc)[:200]}).encode())
            return
        self._send(200, json.dumps(out, separators=(",", ":")).encode())


class TLSServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, admission: Admission, ssl_ctx: ssl.SSLContext):
        self.admission = admission
        self.ssl_ctx = ssl_ctx
        super().__init__(addr, Handler)

    def finish_request(self, request, client_address):
        # TLS handshake in the per-connection thread, not in the accept loop.
        try:
            request.settimeout(10)
            try:
                request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            tls = self.ssl_ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            sys.stderr.write("TLS handshake failed from %s: %r\n" % (client_address, exc))
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass


def server_ssl_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def make_server(bind: str, port: int, admission: Admission, certfile: str, keyfile: str) -> TLSServer:
    return TLSServer((bind, int(port)), admission, server_ssl_context(certfile, keyfile))


def main() -> None:
    env = os.environ
    namespace = env.get("WEBHOOK_NAMESPACE", "")
    timeout_s = float(env.get("WEBHOOK_API_TIMEOUT_S", "1.0"))
    budget_s = float(env.get("WEBHOOK_BUDGET_S", "3.5"))
    if not (0 < timeout_s <= budget_s < 5):
        raise SystemExit("need 0 < WEBHOOK_API_TIMEOUT_S <= WEBHOOK_BUDGET_S < 5 (webhook timeoutSeconds)")
    exempt = [u.strip() for u in env.get("WEBHOOK_EXEMPT_USERS", "").split(",") if u.strip()]
    client = KubeClient.in_cluster(timeout_s)
    log = JsonlLog(env.get("WEBHOOK_LOG_PATH", "/var/log/e38v2-webhook/webhook_log.jsonl"))
    admission = Admission(namespace, client, log, exempt, budget_s)
    srv = make_server(env.get("WEBHOOK_BIND", "127.0.0.1"), int(env.get("WEBHOOK_PORT", "8443")), admission,
                      env.get("WEBHOOK_TLS_CERT", "/tls/tls.crt"), env.get("WEBHOOK_TLS_KEY", "/tls/tls.key"))
    sys.stderr.write("e38v2-webhook serving namespace=%s on %s:%s exempt=%s\n"
                     % (namespace, srv.server_address[0], srv.server_address[1], sorted(admission.exempt)))
    srv.serve_forever()


if __name__ == "__main__":
    main()
