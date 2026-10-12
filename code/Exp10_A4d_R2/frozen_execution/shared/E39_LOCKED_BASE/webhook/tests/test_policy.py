import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import policy  # noqa: E402


def vals(**over):
    v = {cm: "true" for cm in policy.CONFIGMAPS}
    for k, x in over.items():
        v[k.replace("_", "-")] = x
    return v


class PolicyTests(unittest.TestCase):
    def test_all_true_allows(self):
        ok, reason = policy.evaluate(vals())
        self.assertTrue(ok, reason)

    def test_exhaustive_truth_table_matches_spec(self):
        for bits in itertools.product((False, True), repeat=8):
            a, b, c, d, e, f, scope, app = bits
            v = {("evidence-" + k): ("true" if x else "false") for k, x in zip("abcdef", bits[:6])}
            v["response-scope"] = "true" if scope else "false"
            v["application-policy"] = "true" if app else "false"
            expected = ((a and b) or (c and d) or (e and f)) and scope and app
            ok, reason = policy.evaluate(v)
            self.assertEqual(ok, expected, (bits, reason))
            self.assertTrue(reason.startswith("allow" if expected else "deny"))

    def test_each_disjunct_alone_suffices(self):
        for x, y in policy.DISJUNCTS:
            v = {("evidence-" + k): "false" for k in policy.EVIDENCE}
            v.update({"evidence-" + x: "true", "evidence-" + y: "true",
                      "response-scope": "true", "application-policy": "true"})
            self.assertTrue(policy.evaluate(v)[0])

    def test_async_scope_denies(self):
        ok, reason = policy.evaluate(vals(response_scope="false"))
        self.assertFalse(ok)
        self.assertIn("scope_not_allowed", reason)

    def test_async_app_policy_denies(self):
        ok, reason = policy.evaluate(vals(application_policy="false"))
        self.assertFalse(ok)
        self.assertIn("app_policy_not_allowed", reason)

    def test_async_evidence_last_literal(self):
        # before gate: c=false, e=false -> J = a AND b; writer sets a=false.
        pre = vals(evidence_c="false", evidence_e="false")
        self.assertTrue(policy.evaluate(pre)[0])
        ok, reason = policy.evaluate(dict(pre, **{"evidence-a": "false"}))
        self.assertFalse(ok)
        self.assertIn("J_false", reason)

    def test_missing_denies_even_if_irrelevant(self):
        for cm in policy.CONFIGMAPS:
            v = vals()
            del v[cm]
            ok, reason = policy.evaluate(v)
            self.assertFalse(ok, cm)
            self.assertIn("missing_or_unparseable", reason)
            self.assertIn(cm, reason)

    def test_unparseable_denies(self):
        for bad in ("TRUE", "True", " true", "1", "", "yes", True, 1, None, ["true"]):
            for cm in policy.CONFIGMAPS:
                v = vals()
                v[cm] = bad
                self.assertFalse(policy.evaluate(v)[0], (cm, bad))

    def test_non_mapping(self):
        self.assertFalse(policy.evaluate(None)[0])
        self.assertFalse(policy.evaluate(["true"])[0])

    def test_extract_value(self):
        self.assertEqual(policy.extract_value("evidence-a", {"data": {"value": "true"}}), "true")
        self.assertEqual(policy.extract_value("response-scope", {"data": {"allowed": "false"}}), "false")
        self.assertIsNone(policy.extract_value("response-scope", {"data": {"value": "true"}}))
        self.assertIsNone(policy.extract_value("evidence-a", {"data": None}))
        self.assertIsNone(policy.extract_value("evidence-a", {}))
        self.assertIsNone(policy.extract_value("evidence-a", None))

    def test_eight_configmaps(self):
        self.assertEqual(len(policy.CONFIGMAPS), 8)
        self.assertEqual(set(policy.CONFIGMAPS), {"evidence-a", "evidence-b", "evidence-c", "evidence-d",
                                                  "evidence-e", "evidence-f", "response-scope",
                                                  "application-policy"})


if __name__ == "__main__":
    unittest.main()
