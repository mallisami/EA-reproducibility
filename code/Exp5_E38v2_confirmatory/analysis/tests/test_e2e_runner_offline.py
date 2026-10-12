"""E38v2 integration: runner --offline-selftest output -> oracle -> analysis (gate must pass).

The runner (piece 3) runs a 1-rep stage-1 schedule (152 trials: all arms x conditions x deltas +
controls) against its in-process fake apiserver, which emits audit.k8s.io/v1 ResponseComplete
events and e38v2.webhook.v1 entries in the live formats. The analysis (piece 1) must accept the
files unchanged. Requires the runner venv (jsonschema); skipped when it is not present.
"""
import csv
import hashlib
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

import analyze_e38v2 as an
import oracle as orc

WS = Path(__file__).resolve().parents[2]
RUNNER_PY = os.environ.get("E38V2_RUNNER_PY", str(WS / "venv_runner" / "bin" / "python"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def selftest(tmp_path_factory):
    if not Path(RUNNER_PY).is_file() or not (WS / "src" / "e38v2_runner.py").is_file():
        pytest.skip("runner venv / src not available")
    root = tmp_path_factory.mktemp("e2e")
    rd = root / "results" / "e2eself"
    r = subprocess.run([RUNNER_PY, str(WS / "src" / "e38v2_runner.py"), "--offline-selftest",
                        "--output-dir", str(rd), "--seed", "4242", "--run-id", "e2eself", "--selftest-reps", "1"],
                       capture_output=True, text=True, cwd=str(WS / "src"), timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    (root / "protocol").mkdir()
    shutil.copy(WS / "protocol" / "ANALYSIS_PLAN_E38v2.txt", root / "protocol" / "ANALYSIS_PLAN_E38v2.txt")
    lock = {"status": "LOCKED-e2e-test", "run_id": "e2eself", "schedule_seed": 4242,
            "schedule_sha256": sha(rd / "SCHEDULE.json"),
            "analysis_plan_sha256": sha(root / "protocol" / "ANALYSIS_PLAN_E38v2.txt")}
    (root / "protocol" / "LOCK_E38v2.json").write_text(json.dumps(lock))
    return root, rd, json.loads(r.stdout[r.stdout.index("{"):])


def test_selftest_files_and_oracle_joins(selftest):
    root, rd, summary = selftest
    assert summary["trials"] == 152 and summary["infra_error"] == 0
    trials, wh, au, errs = orc.load_inputs(rd / "RAW_TRIALS.jsonl", rd / "webhook_log.jsonl", rd / "audit.log")
    assert not any(errs.values())
    rows = orc.run_oracle(trials, au, wh)
    c = Counter(r["primary_class"] for r in rows)
    assert c["INFRA_ERROR"] == 0
    # every dispatched DELETE and every writer PATCH joins its audit event
    for t, r in zip(trials, rows):
        if t["delete"]["dispatched"]:
            assert r["delete_audit_present"] == 1, (t["trial_id"], r["flags"])
        if t["writer"]:
            assert r["writer_audit_present"] == 1
    # A3 dispatched trials join exactly one webhook entry by pod uid; A0-A2 none
    for t, r in zip(trials, rows):
        if t["delete"]["dispatched"] and t["condition"] != "uid_rebind_control":
            if t["arm"] == "A3_dcji_webhook":
                assert r["webhook_request_uid"] and "webhook_joined_by_pod_uid" in r["flags"]
            else:
                assert r["webhook_request_uid"] == "" and "webhook_entry_for_non_webhook_arm" not in r["flags"]
    by = {}
    for r in rows:
        by.setdefault((r["arm"], r["condition"]), []).append(r["primary_class"])
    for arm in ("A0_native_uid", "A1_dcji", "A2_fullrefresh", "A3_dcji_webhook"):
        assert by[(arm, "no_write")] == ["VALID_EXECUTED"]
        assert by[(arm, "uid_rebind_control")] == ["UID_BLOCKED"]
    assert sum(v == "UNKNOWN" for v in c.elements()) <= 3, dict(c)


def test_selftest_passes_analysis_gate(selftest, tmp_path):
    root, rd, _ = selftest
    rc = an.main(["--run-dir", str(rd), "--lock", str(root / "protocol" / "LOCK_E38v2.json"), "--stage", "1",
                  "--out", str(tmp_path / "out"), "--bootstrap-reps", "200", "--design-check", "off"])
    assert rc == 0
    res = json.loads((tmp_path / "out" / "results_E38v2.json").read_text())
    assert res["completeness_gate"]["passed"] and res["completeness_gate"]["n_observed"] == 152
    assert res["confirmatory"] is False
    with open(tmp_path / "out" / "cells_E38v2.csv") as fh:
        cells = list(csv.DictReader(fh))
    assert len(cells) == 4 * (1 + 3 * 12 + 1)
    # strict design check refuses a 1-rep schedule (smoke is never confirmatory)
    assert an.main(["--run-dir", str(rd), "--lock", str(root / "protocol" / "LOCK_E38v2.json"), "--stage", "1",
                    "--out", str(tmp_path / "strict"), "--bootstrap-reps", "200"]) == 2
