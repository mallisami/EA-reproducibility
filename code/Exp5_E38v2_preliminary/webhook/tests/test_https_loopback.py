"""Loopback-only end-to-end test: real KubeClient + real TLS webhook server.

Both a fake kube-apiserver and the webhook bind to 127.0.0.1 on ephemeral
ports; certificates are generated with openssl into a temp dir and removed.
Checks: quorum GETs (no query string / resourceVersion), bearer token, one
persistent keep-alive connection reused across requests, stale-connection
retry, API timeout -> deny, HTTP 404 -> deny, AdmissionReview over HTTPS.
"""
import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import policy  # noqa: E402
import server  # noqa: E402

NS = "e38v2-testrun"
LOOPBACK = "127.0.0.1"


def openssl(*args, cwd):
    subprocess.run(("openssl",) + args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_certs(d):
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt",
            "-days", "1", "-subj", "/CN=e38v2-test-ca", cwd=d)
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls.key", "-out", "tls.csr",
            "-subj", "/CN=127.0.0.1", cwd=d)
    with open(os.path.join(d, "ext.cnf"), "w") as fh:
        fh.write("subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n")
    openssl("x509", "-req", "-in", "tls.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
            "-out", "tls.crt", "-days", "1", "-extfile", "ext.cnf", cwd=d)


class FakeAPI:
    def __init__(self, certdir):
        self.values = {n: "true" for n in policy.CONFIGMAPS}
        self.missing = set()
        self.delay_s = 0.0
        self.paths = []
        self.auth = []
        self.connections = 0
        self.rv = 5000
        self.close_after_next = False
        api = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                api.connections += 1
                super().setup()

            def log_message(self, *a):
                pass

            def do_GET(self):
                api.paths.append(self.path)
                api.auth.append(self.headers.get("Authorization"))
                if api.delay_s:
                    time.sleep(api.delay_s)
                name = self.path.rsplit("/", 1)[-1]
                prefix = "/api/v1/namespaces/%s/configmaps/" % NS
                if not self.path.startswith(prefix) or name in api.missing or name not in api.values:
                    body = json.dumps({"kind": "Status", "code": 404}).encode()
                    code = 404
                else:
                    api.rv += 1
                    body = json.dumps({"kind": "ConfigMap", "apiVersion": "v1",
                                       "metadata": {"name": name, "namespace": NS,
                                                    "resourceVersion": str(api.rv)},
                                       "data": {policy.VALUE_KEY[name]: api.values[name]}}).encode()
                    code = 200
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if api.close_after_next:
                    api.close_after_next = False
                    self.close_connection = True  # server silently drops keep-alive (no Connection: close)

        self.httpd = ThreadingHTTPServer((LOOPBACK, 0), H)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *a: None  # client-side timeouts cause expected EOFs here
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(certdir, "tls.crt"), os.path.join(certdir, "tls.key"))
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(shutil.which("openssl"), "openssl required")
class LoopbackE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="e38v2_wh_test_")
        make_certs(cls.tmp)
        cls.token = os.path.join(cls.tmp, "token")
        with open(cls.token, "w") as fh:
            fh.write("test-token-not-a-secret\n")
        cls.api = FakeAPI(cls.tmp)
        cls.client = server.KubeClient(LOOPBACK, cls.api.port, cls.token, os.path.join(cls.tmp, "ca.crt"),
                                       timeout_s=0.5)
        cls.logpath = os.path.join(cls.tmp, "log", "webhook_log.jsonl")
        cls.admission = server.Admission(NS, cls.client, server.JsonlLog(cls.logpath, echo_stdout=False),
                                         budget_s=2.0)
        cls.srv = server.make_server(LOOPBACK, 0, cls.admission, os.path.join(cls.tmp, "tls.crt"),
                                     os.path.join(cls.tmp, "tls.key"))
        assert cls.srv.server_address[0] == LOOPBACK
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ctx = ssl.create_default_context(cafile=os.path.join(cls.tmp, "ca.crt"))

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.api.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.api.values = {n: "true" for n in policy.CONFIGMAPS}
        self.api.missing = set()
        self.api.delay_s = 0.0
        self.api.paths.clear()

    def post(self, review, conn=None):
        own = conn is None
        if own:
            conn = http.client.HTTPSConnection(LOOPBACK, self.srv.server_address[1], context=self.ctx, timeout=10)
        body = json.dumps(review).encode()
        conn.request("POST", "/validate", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        if own:
            conn.close()
        return resp.status, (json.loads(data) if resp.status == 200 else data)

    def review(self):
        with open(os.path.join(HERE, "fixtures", "delete_target.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def last_log(self):
        with open(self.logpath, encoding="utf-8") as fh:
            return json.loads(fh.read().splitlines()[-1])

    def test_1_allow_and_quorum_reads_keepalive(self):
        before = self.api.connections
        conn = http.client.HTTPSConnection(LOOPBACK, self.srv.server_address[1], context=self.ctx, timeout=10)
        for _ in range(3):  # apiserver -> webhook keep-alive also works
            status, out = self.post(self.review(), conn)
            self.assertEqual(status, 200)
            self.assertTrue(out["response"]["allowed"], out)
        conn.close()
        self.assertEqual(len(self.api.paths), 24)
        for p in self.api.paths:
            self.assertNotIn("?", p, "quorum read must not carry resourceVersion or any query")
            self.assertNotIn("resourceVersion", p)
        self.assertTrue(all(a == "Bearer test-token-not-a-secret" for a in self.api.auth))
        self.assertLessEqual(self.api.connections - before, 1, "API connection must be persistent")
        rec = self.last_log()
        self.assertEqual(rec["decision"], "allow")
        self.assertEqual(len(rec["reads"]), 8)
        self.assertTrue(all(r["resource_version"].isdigit() for r in rec["reads"]))

    def test_2_invalid_scope_denied(self):
        self.api.values["response-scope"] = "false"
        status, out = self.post(self.review())
        self.assertEqual(status, 200)
        self.assertFalse(out["response"]["allowed"])
        self.assertEqual(out["response"]["status"]["code"], 403)
        self.assertIn("scope_not_allowed", self.last_log()["reason"])

    def test_3_missing_configmap_denied(self):
        self.api.missing = {"evidence-f"}
        status, out = self.post(self.review())
        self.assertFalse(out["response"]["allowed"])
        self.assertIn("http_404", self.last_log()["reason"])

    def test_4_api_timeout_denied(self):
        self.api.delay_s = 0.8  # > client timeout 0.5
        t0 = time.monotonic()
        status, out = self.post(self.review())
        self.assertEqual(status, 200)
        self.assertFalse(out["response"]["allowed"])
        self.assertIn("timeout", self.last_log()["reason"])
        self.assertLess(time.monotonic() - t0, 2.5)
        self.api.delay_s = 0.0
        time.sleep(0.5)
        status, out = self.post(self.review())  # recovers with a fresh connection
        self.assertTrue(out["response"]["allowed"])

    def test_5_stale_keepalive_retried_once(self):
        self.post(self.review())
        self.api.close_after_next = True
        self.post(self.review())  # server drops the connection after one GET
        status, out = self.post(self.review())
        self.assertTrue(out["response"]["allowed"], self.last_log())

    def test_6_bad_body_is_http_error(self):
        conn = http.client.HTTPSConnection(LOOPBACK, self.srv.server_address[1], context=self.ctx, timeout=10)
        conn.request("POST", "/validate", body=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_7_healthz(self):
        conn = http.client.HTTPSConnection(LOOPBACK, self.srv.server_address[1], context=self.ctx, timeout=10)
        conn.request("GET", "/healthz")
        r = conn.getresponse()
        self.assertEqual((r.status, r.read()), (200, b"ok"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
