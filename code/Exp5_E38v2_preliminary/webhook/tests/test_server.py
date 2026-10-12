"""Offline tests of the admission handler with a mocked API client (no network)."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import policy  # noqa: E402
import server  # noqa: E402

NS = "e38v2-testrun"
FIX = os.path.join(HERE, "fixtures")
LOG_KEYS = {"schema", "request_uid", "pod_name", "pod_uid", "received_wall_ns", "reads", "decision",
            "reason", "completed_wall_ns",
            # E38v2 integration: which Pod version was admitted (two-phase Pod DELETE diagnosis)
            "pod_resource_version", "pod_terminating"}


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


def cm(name, value, rv):
    key = policy.VALUE_KEY[name]
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": NS, "resourceVersion": str(rv)},
            "data": {key: value}}


class MockClient:
    def __init__(self, values=None, errors=None):
        self.lock = threading.Lock()
        self.values = {n: "true" for n in policy.CONFIGMAPS}
        self.values.update(values or {})
        self.errors = errors or {}
        self.calls = []

    def get_configmap(self, namespace, name, deadline):
        assert self.lock.locked(), "reads must happen under the client lock"
        self.calls.append((namespace, name))
        if name in self.errors:
            raise self.errors[name]
        v = self.values.get(name)
        if v is KeyError:
            return {"kind": "ConfigMap", "metadata": {"resourceVersion": "1"}, "data": {}}
        return cm(name, v, 1000 + len(self.calls))


class ListLog:
    def __init__(self):
        self.records = []

    def write(self, rec):
        self.records.append(json.loads(json.dumps(rec)))


def make(values=None, errors=None, **kw):
    client = MockClient(values, errors)
    log = ListLog()
    adm = server.Admission(NS, client, log, **kw)
    return adm, client, log


class AdmissionTests(unittest.TestCase):
    def check_response(self, out, uid, allowed):
        self.assertEqual(out["apiVersion"], "admission.k8s.io/v1")
        self.assertEqual(out["kind"], "AdmissionReview")
        self.assertEqual(out["response"]["uid"], uid)
        self.assertIs(out["response"]["allowed"], allowed)
        if not allowed:
            self.assertEqual(out["response"]["status"]["code"], 403)
            self.assertIn("e38v2-webhook", out["response"]["status"]["message"])

    def test_valid_state_allows(self):
        adm, client, log = make()
        rv = load("delete_target.json")
        out = adm.review(rv)
        self.check_response(out, rv["request"]["uid"], True)
        self.assertEqual([n for _, n in client.calls], list(policy.CONFIGMAPS))
        self.assertTrue(all(ns == NS for ns, _ in client.calls))
        self.assertEqual(log.records[0]["decision"], "allow")

    def test_each_invalid_predicate_denies(self):
        cases = [
            {"response-scope": "false"},
            {"application-policy": "false"},
            {"evidence-a": "false", "evidence-c": "false", "evidence-e": "false"},
            {"evidence-b": "false", "evidence-d": "false", "evidence-f": "false"},
            {"evidence-c": "false", "evidence-e": "false", "evidence-a": "false"},
        ]
        for values in cases:
            adm, _, log = make(values)
            rv = load("delete_target.json")
            out = adm.review(rv)
            self.check_response(out, rv["request"]["uid"], False)
            self.assertEqual(log.records[-1]["decision"], "deny", values)

    def test_unparseable_and_missing_key_deny(self):
        for name in policy.CONFIGMAPS:
            for bad in ("TRUE", "", KeyError):
                adm, _, log = make({name: bad})
                out = adm.review(load("delete_target.json"))
                self.assertFalse(out["response"]["allowed"], (name, bad))
                self.assertIn("missing_or_unparseable", log.records[-1]["reason"])

    def test_api_errors_deny(self):
        for err in (server.ApiError("http_404 GET evidence-d"), server.ApiError("timeout: TimeoutError()"),
                    server.ApiError("http_500"), RuntimeError("bug")):
            adm, client, log = make(errors={"evidence-d": err})
            out = adm.review(load("delete_target.json"))
            self.assertFalse(out["response"]["allowed"], err)
            rec = log.records[-1]
            self.assertEqual(rec["decision"], "deny")
            self.assertTrue(rec["reason"].startswith("deny: api_error") or "internal_error" in rec["reason"])
            # reads that succeeded before the failure are still logged
            self.assertEqual([r["object"] for r in rec["reads"]], ["evidence-a", "evidence-b", "evidence-c"])
            self.assertEqual(len(client.calls), 4)  # stops at first failure

    def test_non_target_namespace_allows_without_reads(self):
        adm, client, log = make({"response-scope": "false"})
        rv = load("delete_other_ns.json")
        out = adm.review(rv)
        self.check_response(out, rv["request"]["uid"], True)
        self.assertEqual(client.calls, [])
        self.assertEqual(log.records, [])

    def test_non_delete_allows(self):
        adm, client, log = make({"response-scope": "false"})
        out = adm.review(load("create_target.json"))
        self.assertTrue(out["response"]["allowed"])
        self.assertEqual(client.calls, [])
        for op in ("UPDATE", "CONNECT"):
            rv = load("delete_target.json")
            rv["request"]["operation"] = op
            self.assertTrue(adm.review(rv)["response"]["allowed"])
        self.assertEqual(client.calls, [])

    def test_non_pod_resource_or_subresource_allows(self):
        adm, client, _ = make({"response-scope": "false"})
        rv = load("delete_target.json")
        rv["request"]["resource"]["resource"] = "configmaps"
        self.assertTrue(adm.review(rv)["response"]["allowed"])
        rv = load("delete_target.json")
        rv["request"]["subResource"] = "eviction"
        self.assertTrue(adm.review(rv)["response"]["allowed"])
        self.assertEqual(client.calls, [])

    def test_exempt_identities_allow(self):
        reset = "system:serviceaccount:e38v2-system:e38v2-reset"
        adm, client, log = make({"response-scope": "false"}, exempt_users=[reset])
        for user in (reset, "system:node:e38v2-control-plane",
                     "system:serviceaccount:kube-system:namespace-controller",
                     "system:serviceaccount:kube-system:generic-garbage-collector"):
            rv = load("delete_target.json")
            rv["request"]["userInfo"]["username"] = user
            self.assertTrue(adm.review(rv)["response"]["allowed"], user)
        self.assertEqual(client.calls, [])
        # responder is NOT exempt
        self.assertFalse(adm.review(load("delete_target.json"))["response"]["allowed"])

    def test_already_terminating_allows(self):
        adm, client, _ = make({"response-scope": "false"})
        rv = load("delete_target.json")
        rv["request"]["oldObject"]["metadata"]["deletionTimestamp"] = "2026-09-26T00:00:00Z"
        self.assertTrue(adm.review(rv)["response"]["allowed"])
        self.assertEqual(client.calls, [])
        # the unevaluated second-phase call is still logged (no reads, pod_terminating True)
        rec = adm.log.records[-1]
        self.assertEqual(set(rec), LOG_KEYS)
        self.assertTrue(rec["pod_terminating"])
        self.assertEqual((rec["decision"], rec["reads"]), ("allow", []))
        self.assertIn("not evaluated", rec["reason"])

    def test_log_line_schema(self):
        adm, _, log = make({"evidence-a": "false"})
        rv = load("delete_target.json")
        adm.review(rv)
        rec = log.records[0]
        self.assertEqual(set(rec), LOG_KEYS)
        self.assertEqual(rec["schema"], "e38v2.webhook.v1")
        self.assertEqual(rec["request_uid"], rv["request"]["uid"])
        self.assertEqual(rec["pod_name"], "e38v2-target")
        self.assertEqual(rec["pod_uid"], rv["request"]["oldObject"]["metadata"]["uid"])
        self.assertIsInstance(rec["received_wall_ns"], int)
        self.assertIsInstance(rec["completed_wall_ns"], int)
        self.assertGreaterEqual(rec["completed_wall_ns"], rec["received_wall_ns"])
        self.assertIn(rec["decision"], ("allow", "deny"))
        self.assertIsInstance(rec["reason"], str)
        self.assertEqual(len(rec["reads"]), 8)
        # E38v2 integration: every read also carries wall-clock send/receive stamps (ns)
        for r in rec["reads"]:
            self.assertEqual(set(r), {"object", "resource_version", "value", "t_send_wall_ns", "t_recv_wall_ns"})
            self.assertTrue(all(isinstance(r[k], str) for k in ("object", "resource_version", "value")))
            self.assertIsInstance(r["t_send_wall_ns"], int)
            self.assertIsInstance(r["t_recv_wall_ns"], int)
            self.assertLessEqual(rec["received_wall_ns"], r["t_send_wall_ns"])
            self.assertLessEqual(r["t_send_wall_ns"], r["t_recv_wall_ns"])
            self.assertLessEqual(r["t_recv_wall_ns"], rec["completed_wall_ns"])
        sends = [r["t_send_wall_ns"] for r in rec["reads"]]
        self.assertEqual(sends, sorted(sends))
        self.assertEqual({k: rec["reads"][0][k] for k in ("object", "resource_version", "value")},
                         {"object": "evidence-a", "resource_version": "1001", "value": "false"})

    def test_jsonl_file_writer(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "webhook_log.jsonl")
            log = server.JsonlLog(path, echo_stdout=False)
            adm = server.Admission(NS, MockClient(), log)
            adm.review(load("delete_target.json"))
            adm.review(load("delete_target.json"))
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(x) for x in fh.read().splitlines()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(set(lines[0]), LOG_KEYS)

    def test_malformed_review_raises_bad_review(self):
        adm, _, _ = make()
        for bad in (None, {}, {"apiVersion": "admission.k8s.io/v1beta1", "kind": "AdmissionReview", "request": {}},
                    {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "request": {"uid": ""}}):
            with self.assertRaises(server.BadReview):
                adm.review(bad)

    def test_namespace_guard(self):
        for bad in ("", "default", "e38v2-system", "e38-old", "E38V2-X"):
            with self.assertRaises(ValueError):
                server.Admission(bad, MockClient(), ListLog())

    def test_deadline_denies(self):
        adm, client, log = make(budget_s=0.0)
        # real KubeClient raises on expired deadline; emulate via a client that checks it
        import time

        class DeadlineClient(MockClient):
            def get_configmap(self, namespace, name, deadline):
                if time.monotonic() >= deadline:
                    raise server.ApiError("deadline exceeded before GET " + name)
                return super().get_configmap(namespace, name, deadline)
        adm.client = DeadlineClient()
        out = adm.review(load("delete_target.json"))
        self.assertFalse(out["response"]["allowed"])
        self.assertIn("deadline", log.records[-1]["reason"])


if __name__ == "__main__":
    unittest.main()
