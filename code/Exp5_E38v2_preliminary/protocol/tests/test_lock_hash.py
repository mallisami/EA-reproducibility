import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.dirname(HERE)
sys.path.insert(0, PROTO)
import lock_hash  # noqa: E402

E37 = "c54f4331cf0d73e7cde1b5da564480bb4911d4889b4f06c9670e1da4efd3cb2f"


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = lock_hash.main([str(x) for x in argv])
    return rc, buf.getvalue()


class LockHashTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="e38v2_lock_test_")
        with open(os.path.join(PROTO, "LOCK_TEMPLATE_E38v2.json"), encoding="utf-8") as fh:
            tpl = json.load(fh)
        for entry in tpl["files"]:
            rel = entry.get("path") or entry["glob"].replace("*", "x")
            p = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("content of " + rel + "\n")
        # fake pinned file whose hash will NOT match -> use a template copy with its real hash
        pin = os.path.join(self.root, tpl["pinned"][0]["path"])
        os.makedirs(os.path.dirname(pin), exist_ok=True)
        with open(pin, "w") as fh:
            fh.write("pinned\n")
        tpl["pinned"][0]["expected_sha256"] = lock_hash.sha256_file(pin)
        self.tpl = os.path.join(self.root, "tpl.json")
        with open(self.tpl, "w") as fh:
            json.dump(tpl, fh)
        self.out = os.path.join(self.root, "LOCK.json")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_template_pins_e37(self):
        with open(os.path.join(PROTO, "LOCK_TEMPLATE_E38v2.json"), encoding="utf-8") as fh:
            tpl = json.load(fh)
        self.assertEqual(tpl["pinned"][0]["expected_sha256"], E37)
        self.assertIsNone(tpl["design"]["schedule_seed"])
        self.assertIn(20260912, tpl["design"]["forbidden_schedule_seeds"])
        self.assertTrue(all(s[k] is None for s in tpl["signers"] for k in s))

    def test_fill_verify_roundtrip_and_tamper(self):
        rc, out = run("fill", self.tpl, self.out, "--root", self.root, "--seed", 424242)
        self.assertEqual(rc, 0, out)
        rc, out = run("verify", self.out, "--root", self.root)
        self.assertEqual(rc, 0, out)
        # signing does not change lock_sha256
        lock = json.load(open(self.out))
        lock["signers"][0].update(name="A", signed_utc="2026-09-27T00:00:00Z")
        json.dump(lock, open(self.out, "w"))
        self.assertEqual(run("verify", self.out, "--root", self.root)[0], 0)
        self.assertEqual(run("verify", self.out, "--root", self.root, "--require-signed")[0], 1)
        # tamper with code
        with open(os.path.join(self.root, "webhook/policy.py"), "a") as fh:
            fh.write("# edit\n")
        rc, out = run("verify", self.out, "--root", self.root)
        self.assertEqual(rc, 1)
        self.assertIn("hash mismatch: webhook/policy.py", out)

    def test_added_file_in_glob_detected(self):
        self.assertEqual(run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)[0], 0)
        with open(os.path.join(self.root, "src/flashir_core/new.py"), "w") as fh:
            fh.write("x\n")
        rc, out = run("verify", self.out, "--root", self.root)
        self.assertEqual(rc, 1)
        self.assertIn("unlocked file", out)

    def test_design_edit_detected(self):
        self.assertEqual(run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)[0], 0)
        lock = json.load(open(self.out))
        lock["design"]["sample_sizes"]["stage2_target_per_cell"] = 50
        json.dump(lock, open(self.out, "w"))
        rc, out = run("verify", self.out, "--root", self.root)
        self.assertEqual(rc, 1)
        self.assertIn("lock_sha256 mismatch", out)

    def test_forbidden_or_missing_seed(self):
        rc, out = run("fill", self.tpl, self.out, "--root", self.root, "--seed", 20260912)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.out))
        rc, out = run("fill", self.tpl, self.out, "--root", self.root)
        self.assertEqual(rc, 1)

    def test_pinned_mismatch(self):
        with open(os.path.join(self.root, "frozen_ref/source_provenance/E37_F5_PHYS_ORIGINAL.json"), "w") as fh:
            fh.write("changed\n")
        rc, out = run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)
        self.assertEqual(rc, 1)
        self.assertIn("pinned hash mismatch", out)

    def test_missing_file(self):
        os.remove(os.path.join(self.root, "src/writer.py"))
        self.assertEqual(run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)[0], 1)
        self.assertEqual(run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7, "--allow-missing")[0], 0)
        self.assertEqual(run("verify", self.out, "--root", self.root)[0], 1)

    def _sched(self, name, seed, run_id="r1", stage=1):
        p = os.path.join(self.root, "results", run_id, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump({"schema": "e38v2.schedule.v1", "run_id": run_id, "schedule_seed": seed, "stage": stage,
                       "trials": [{"trial_id": 1, "stage": stage}]}, fh)
        return p

    def test_integration_fields_and_stage2(self):
        s1 = self._sched("SCHEDULE.json", 424242)
        rc, out = run("fill", self.tpl, self.out, "--root", self.root, "--seed", 424242, "--run-id", "r1",
                      "--schedule", "results/r1/SCHEDULE.json", "--analysis-plan", "protocol/ANALYSIS_PLAN_E38v2.txt")
        self.assertEqual(rc, 0, out)
        lock = json.load(open(self.out))
        self.assertTrue(lock["status"].startswith("LOCKED-"))
        self.assertEqual((lock["schedule_seed"], lock["run_id"]), (424242, "r1"))
        self.assertEqual(lock["schedule_sha256"], lock_hash.sha256_file(s1))
        self.assertEqual(len(lock["analysis_plan_sha256"]), 64)
        self.assertEqual(run("verify", self.out, "--root", self.root)[0], 0)
        # schedule edited after lock -> verify fails
        orig = open(s1).read()
        open(s1, "a").write(" ")
        rc, out = run("verify", self.out, "--root", self.root)
        self.assertEqual(rc, 1)
        self.assertIn("hash mismatch: schedule", out)
        open(s1, "w").write(orig)
        # stage 2: wrong seed refused, right seed recorded, digest changes, verify ok, tamper detected
        bad = self._sched("SCHEDULE_STAGE2_bad.json", 1, stage=2)
        self.assertEqual(run("add-stage2", self.out, "--schedule2", bad, "--root", self.root)[0], 1)
        s2 = self._sched("SCHEDULE_STAGE2.json", 424242, stage=2)
        d1 = json.load(open(self.out))["lock_sha256"]
        rc, out = run("add-stage2", self.out, "--schedule2", "results/r1/SCHEDULE_STAGE2.json", "--root", self.root)
        self.assertEqual(rc, 0, out)
        lock = json.load(open(self.out))
        self.assertEqual(lock["lock_sha256_stage1"], d1)
        self.assertNotEqual(lock["lock_sha256"], d1)
        self.assertEqual(run("verify", self.out, "--root", self.root)[0], 0)
        self.assertEqual(run("add-stage2", self.out, "--schedule2", s2, "--root", self.root)[0], 1)  # only once
        open(s2, "a").write(" ")
        self.assertEqual(run("verify", self.out, "--root", self.root)[0], 1)

    def test_schedule_seed_mismatch_refused(self):
        self._sched("SCHEDULE.json", 5)
        rc, out = run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7,
                      "--schedule", "results/r1/SCHEDULE.json")
        self.assertEqual(rc, 1)
        self.assertIn("schedule seed", out)

    def test_no_overwrite(self):
        self.assertEqual(run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)[0], 0)
        with self.assertRaises(SystemExit):
            run("fill", self.tpl, self.out, "--root", self.root, "--seed", 7)


if __name__ == "__main__":
    unittest.main()
