import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lock_hash_e39 as lh  # noqa: E402


def _tree(tmp):
    (tmp / "analysis").mkdir()
    (tmp / "analysis" / "a.py").write_text("x = 1\n")
    (tmp / "protocol").mkdir()
    (tmp / "protocol" / "ANALYSIS_PLAN_E39.txt").write_text("plan\n")
    (tmp / "results").mkdir()
    sch = {"schema": "e39.schedule.v1", "run_id": "r1", "schedule_seed": 424242,
           "trials": [{"trial_id": 1, "stage": 1, "experiment": "A"}, {"trial_id": 2, "stage": 1, "experiment": "G"}]}
    (tmp / "results" / "SCHEDULE.json").write_text(json.dumps(sch))
    tpl = {"schema": "e39.lock.v1", "root": str(tmp), "files": [{"glob": "analysis/*.py", "role": "analysis"},
                                                                 {"path": "protocol/ANALYSIS_PLAN_E39.txt", "role": "plan"}],
           "pinned": [], "design": {"schedule_seed": None, "forbidden_schedule_seeds": [20260912, 1772720255]},
           "signers": [{"name": None, "signed_utc": None}], "lock_sha256": None}
    (tmp / "protocol" / "T.json").write_text(json.dumps(tpl))
    return tmp


def _fill(tmp, *extra, seed=424242):
    return lh.main(["fill", str(tmp / "protocol" / "T.json"), str(tmp / "protocol" / "LOCK_E39.json"), "--seed", str(seed),
                    "--run-id", "r1", "--schedule", "results/SCHEDULE.json",
                    "--analysis-plan", "protocol/ANALYSIS_PLAN_E39.txt", *extra])


def test_fill_refuses_without_rerun_seed(tmp_path):
    assert _fill(_tree(tmp_path)) == 1


def test_fill_with_forbid_seed_then_verify(tmp_path):
    t = _tree(tmp_path)
    assert _fill(t, "--forbid-seed", "111") == 0
    lk = json.loads((t / "protocol" / "LOCK_E39.json").read_text())
    assert 111 in lk["design"]["forbidden_schedule_seeds"] and lk["status"].startswith("LOCKED")
    assert lk["schedule_experiments"] == ["A", "G"]
    assert lh.main(["verify", str(t / "protocol" / "LOCK_E39.json")]) == 0


def test_fill_reads_rerun_lock_seed(tmp_path):
    t = _tree(tmp_path)
    (t / "rerun_lock.json").write_text(json.dumps({"schedule_seed": 555}))
    assert _fill(t, "--e38v2r-lock", str(t / "rerun_lock.json")) == 0
    lk = json.loads((t / "protocol" / "LOCK_E39.json").read_text())
    assert 555 in lk["design"]["forbidden_schedule_seeds"] and lk["design"]["e38v2r_lock_sha256"]


@pytest.mark.parametrize("seed", [20260912, 1772720255, 777])
def test_fill_refuses_forbidden_seeds(tmp_path, seed):
    t = _tree(tmp_path)
    assert _fill(t, "--forbid-seed", "777", seed=seed) == 1


def test_verify_detects_code_change_and_new_file(tmp_path):
    t = _tree(tmp_path)
    assert _fill(t, "--forbid-seed", "111") == 0
    lock = str(t / "protocol" / "LOCK_E39.json")
    (t / "analysis" / "b.py").write_text("new\n")
    assert lh.main(["verify", lock]) == 1
    (t / "analysis" / "b.py").unlink()
    (t / "analysis" / "a.py").write_text("x = 2\n")
    assert lh.main(["verify", lock]) == 1


def test_signing_does_not_change_digest_but_edits_do(tmp_path):
    t = _tree(tmp_path)
    assert _fill(t, "--forbid-seed", "111") == 0
    p = t / "protocol" / "LOCK_E39.json"
    lk = json.loads(p.read_text())
    lk["signers"] = [{"name": "X", "signed_utc": "2026-09-26T00:00:00Z"}]
    p.write_text(json.dumps(lk))
    assert lh.main(["verify", str(p), "--require-signed"]) == 0
    lk["design"]["schedule_seed"] = 424243
    lk["schedule_seed"] = 424243
    p.write_text(json.dumps(lk))
    assert lh.main(["verify", str(p)]) == 1


def test_add_stage2_only_E39A_and_high_ids(tmp_path):
    t = _tree(tmp_path)
    assert _fill(t, "--forbid-seed", "111") == 0
    lock = str(t / "protocol" / "LOCK_E39.json")
    bad = {"run_id": "r1", "schedule_seed": 424242, "trials": [{"trial_id": 100001, "stage": 2, "experiment": "B"}]}
    (t / "results" / "S2.json").write_text(json.dumps(bad))
    assert lh.main(["add-stage2", lock, "--schedule2", "results/S2.json"]) == 1
    low = {"run_id": "r1", "schedule_seed": 424242, "trials": [{"trial_id": 5, "stage": 2, "experiment": "A"}]}
    (t / "results" / "S2.json").write_text(json.dumps(low))
    assert lh.main(["add-stage2", lock, "--schedule2", "results/S2.json"]) == 1
    good = {"run_id": "r1", "schedule_seed": 424242, "trials": [{"trial_id": 100001, "stage": 2, "experiment": "A"}]}
    (t / "results" / "S2.json").write_text(json.dumps(good))
    assert lh.main(["add-stage2", lock, "--schedule2", "results/S2.json"]) == 0
    lk = json.loads(Path(lock).read_text())
    assert lk["stage2_schedule_sha256"] and lk["lock_sha256_stage1"] != lk["lock_sha256"]
    assert lh.main(["verify", lock]) == 0
