"""E39-E: webhook evaluation of networkpolicies CREATE (offline, mocked API client)."""
import copy
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import policy  # noqa: E402
import server  # noqa: E402

NS = "e39-testrun"
RESPONDER = "system:serviceaccount:e38v2-system:e38v2-responder"
REVIEW = {
    "apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview",
    "request": {
        "uid": "np-req-1", "kind": {"group": "networking.k8s.io", "version": "v1", "kind": "NetworkPolicy"},
        "resource": {"group": "networking.k8s.io", "version": "v1", "resource": "networkpolicies"},
        "name": "e39-isolate-7", "namespace": NS, "operation": "CREATE",
        "userInfo": {"username": RESPONDER},
        "object": {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                   "metadata": {"name": "e39-isolate-7", "namespace": NS,
                                "labels": {"e38v2-webhook": "enforce", "e39-trial": "r-t7"}},
                   "spec": {"podSelector": {"matchLabels": {"e39-trial": "r-t7"}},
                            "policyTypes": ["Ingress", "Egress"]}},
        "oldObject": None, "dryRun": False,
    }}
KEYS = {"schema", "request_uid", "resource", "operation", "object_name", "netpol_pod_selector", "pod_name",
        "pod_uid", "pod_resource_version", "pod_terminating", "received_wall_ns", "reads", "decision",
        "reason", "completed_wall_ns"}


class Mock:
    def __init__(self, values=None, errors=None, delay_s=0.0):
        self.lock = threading.Lock()
        self.values = {n: "true" for n in policy.CONFIGMAPS}
        self.values.update(values or {})
        self.errors = errors or {}
        self.calls = []
        self.delay_s = delay_s

    def get_configmap(self, namespace, name, deadline):
        assert self.lock.locked()
        self.calls.append((namespace, name))
        if self.delay_s:
            time.sleep(self.delay_s)
        if time.monotonic() > deadline:
            raise server.ApiError("deadline exceeded before GET %s" % name)
        if name in self.errors:
            raise self.errors[name]
        return {"kind": "ConfigMap", "metadata": {"name": name, "resourceVersion": str(500 + len(self.calls))},
                "data": {policy.VALUE_KEY[name]: self.values[name]}}


class Log:
    def __init__(self):
        self.records = []

    def write(self, r):
        self.records.append(r)


def make(values=None, errors=None, **kw):
    c, lg = Mock(values, errors), Log()
    return server.Admission(NS, c, lg, **kw), c, lg


def review(**changes):
    r = copy.deepcopy(REVIEW)
    r["request"].update(changes)
    return r


class NetpolAdmissionTests(unittest.TestCase):
    def test_valid_allows_with_eight_reads(self):
        adm, c, lg = make()
        out = adm.review(review())
        self.assertTrue(out["response"]["allowed"])
        self.assertEqual([n for _, n in c.calls], list(policy.CONFIGMAPS))
        rec = lg.records[-1]
        self.assertEqual(set(rec), KEYS)
        self.assertEqual((rec["resource"], rec["operation"], rec["object_name"]),
                         ("networkpolicies", "CREATE", "e39-isolate-7"))
        self.assertEqual(rec["netpol_pod_selector"], {"matchLabels": {"e39-trial": "r-t7"}})
        self.assertEqual(len(rec["reads"]), 8)
        self.assertEqual(rec["decision"], "allow")

    def test_invalid_predicates_deny(self):
        for values in ({"response-scope": "false"}, {"application-policy": "false"},
                       {"evidence-a": "false", "evidence-c": "false", "evidence-e": "false"},
                       {"evidence-b": "false", "evidence-d": "false", "evidence-f": "false"}):
            adm, _, lg = make(values)
            out = adm.review(review())
            self.assertFalse(out["response"]["allowed"], values)
            self.assertEqual(out["response"]["status"]["code"], 403)
            self.assertIn("e38v2-webhook", out["response"]["status"]["message"])
            self.assertEqual(lg.records[-1]["decision"], "deny")

    def test_fail_closed_on_api_error_and_bad_values(self):
        adm, c, lg = make(errors={"evidence-c": server.ApiError("http_500 GET evidence-c")})
        self.assertFalse(adm.review(review())["response"]["allowed"])
        self.assertTrue(lg.records[-1]["reason"].startswith("deny: api_error"))
        self.assertEqual(len(c.calls), 3)
        adm, _, lg = make(errors={"evidence-a": RuntimeError("bug")})
        self.assertFalse(adm.review(review())["response"]["allowed"])
        self.assertIn("internal_error", lg.records[-1]["reason"])
        adm, _, lg = make({"response-scope": "TRUE"})
        self.assertFalse(adm.review(review())["response"]["allowed"])
        self.assertIn("missing_or_unparseable", lg.records[-1]["reason"])

    def test_deadline_denies(self):
        c, lg = Mock(delay_s=0.02), Log()
        adm = server.Admission(NS, c, lg, budget_s=0.05)
        self.assertFalse(adm.review(review())["response"]["allowed"])
        self.assertIn("deny", lg.records[-1]["reason"])

    def test_scoping(self):
        adm, c, lg = make({"response-scope": "false"})
        # other namespace
        self.assertTrue(adm.review(review(namespace="default"))["response"]["allowed"])
        # netpol UPDATE / DELETE are not evaluated
        for op in ("UPDATE", "DELETE"):
            self.assertTrue(adm.review(review(operation=op))["response"]["allowed"], op)
        # same resource name in another group is not a NetworkPolicy
        r = review()
        r["request"]["resource"]["group"] = "example.com"
        self.assertTrue(adm.review(r)["response"]["allowed"])
        self.assertEqual(c.calls, [])
        self.assertEqual(lg.records, [])

    def test_exempt_reset_identity(self):
        reset = "system:serviceaccount:e38v2-system:e38v2-reset"
        adm, c, _ = make({"response-scope": "false"}, exempt_users=[reset])
        r = review()
        r["request"]["userInfo"]["username"] = reset
        self.assertTrue(adm.review(r)["response"]["allowed"])
        self.assertEqual(c.calls, [])
        self.assertFalse(adm.review(review())["response"]["allowed"])

    def test_pod_delete_path_unchanged(self):
        # a pods CREATE is still not evaluated (E38v2 behaviour)
        adm, c, _ = make({"response-scope": "false"})
        r = review()
        r["request"]["resource"] = {"group": "", "version": "v1", "resource": "pods"}
        self.assertTrue(adm.review(r)["response"]["allowed"])
        self.assertEqual(c.calls, [])

    def test_namespace_guard_e39(self):
        server.Admission("e39-r20260926a", Mock(), Log())
        server.Admission("e38v2-r20260926a", Mock(), Log())
        for bad in ("e39-system", "e38v2-system", "e39", "e39-", "E39-X", "e40-x"):
            with self.assertRaises(ValueError, msg=bad):
                server.Admission(bad, Mock(), Log())

    def test_name_from_object_when_request_name_missing(self):
        adm, _, lg = make()
        r = review()
        del r["request"]["name"]
        adm.review(r)
        self.assertEqual(lg.records[-1]["object_name"], "e39-isolate-7")


if __name__ == "__main__":
    unittest.main()
