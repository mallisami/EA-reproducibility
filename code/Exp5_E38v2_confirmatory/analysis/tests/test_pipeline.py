"""End-to-end tests on synthetic runs: oracle vs simulator truth, analysis outputs, completeness gate
refusals, stage-1 -> stage-2 top-up cycle."""
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import analyze_e38v2 as an
import stage2_topup as s2
import synth
from e38v2_common import OUTCOMES, load_jsonl

ROOT = Path(__file__).resolve().parents[1]
REPS = "200"


def run_analysis(root, out, stage=1, schedules=None, extra=()):
    rd = Path(root) / "results" / "synthrun1"
    argv = ["--run-dir", str(rd), "--lock", str(Path(root) / "protocol" / "LOCK_E38v2.json"),
            "--stage", str(stage), "--out", str(out), "--bootstrap-reps", REPS]
    for s in (schedules or []):
        argv += ["--schedule", str(s)]
    return an.main(argv + list(extra))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def relock(root, **upd):
    lp = Path(root) / "protocol" / "LOCK_E38v2.json"
    lock = json.loads(lp.read_text())
    lock["schedule_sha256"] = sha(Path(root) / "results" / "synthrun1" / "SCHEDULE.json")
    lock.update(upd)
    lp.write_text(json.dumps(lock))


def rewrite_jsonl(p, fn):
    recs = load_jsonl(p)[0]
    recs = fn(recs)
    Path(p).write_text("".join(json.dumps(r) + "\n" for r in recs))


# ----------------------------------------------------------------------------- full design
@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("full")
    info = synth.generate_run(root, design="full")
    out1 = root / "out1"
    rc = run_analysis(root, out1, 1)
    return root, info, out1, rc


def test_full_stage1_runs(full_run):
    root, info, out1, rc = full_run
    assert rc == 0
    for f in ("results_E38v2.json", "cells_E38v2.csv", "oracle_trials_E38v2.csv", "exposure_window_E38v2.csv",
              "availability_E38v2.csv", "webhook_latency_E38v2.csv", "writer_release_E38v2.csv",
              "agreement_E38v2.csv", "agreement_tertiary_E38v2.csv", "stage2_candidates_E38v2.csv"):
        assert (out1 / f).is_file(), f
    assert not (out1 / "REFUSED_E38v2.json").exists()
    res = json.loads((out1 / "results_E38v2.json").read_text())
    assert res["completeness_gate"]["passed"] and res["confirmatory"]
    # plan v1.2: tertiary (v1 secondary rule) reported alongside; no pod-event file in the synthetic run
    assert res["analysis_version"] == "e38v2.analysis.v1.2" and res["oracle_version"] == "e38v2.oracle.v1.2"
    assert res["oracle_agreement_tertiary"]["compared_with"] == "tertiary_class"
    assert res["pod_events"]["supplied"] is False
    assert all("tertiary" in c and "agreement_tertiary" in c for c in res["cells"])
    # the corrected secondary agrees with the primary at least as often as the v1 rule
    assert res["oracle_agreement"]["prop_exact_agree"] >= res["oracle_agreement_tertiary"]["prop_exact_agree"]
    assert res["completeness_gate"]["n_planned"] == 5812 == res["completeness_gate"]["n_observed"]
    assert len(res["cells"]) == 4 * (1 + 3 * 12 + 1)
    # every outcome class is exercised by the synthetic run
    for k in OUTCOMES:
        assert res["overall_counts_primary"][k] > 0, k


def test_full_oracle_matches_simulator_truth(full_run):
    root, info, out1, rc = full_run
    truth = {t["trial_id"]: t for t in load_jsonl(root / "results" / "synthrun1" / "TRUTH.jsonl")[0]}
    with open(out1 / "oracle_trials_E38v2.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(truth)
    bad = [(r["trial_id"], r["primary_class"], truth[int(r["trial_id"])]["truth_primary"], r["primary_reason"])
           for r in rows if r["primary_class"] != truth[int(r["trial_id"])]["truth_primary"]]
    assert not bad, bad[:10]
    byid = {int(r["trial_id"]): r for r in rows}
    p = info["picks"]
    assert byid[p["missing_writer_audit"]]["secondary_reason"] == "time_rule_writer_audit_missing"
    assert byid[p["missing_writer_audit"]]["writer_rv_source"] == "trial"
    assert byid[p["equal_timestamps"]]["secondary_reason"] == "time_tie"
    assert byid[p["webhook_false_abort_0"]]["primary_class"] == "FALSE_ABORT"
    assert byid[p["infra_0"]]["primary_class"] == "INFRA_ERROR"


def test_full_results_content(full_run):
    root, info, out1, rc = full_run
    res = json.loads((out1 / "results_E38v2.json").read_text())
    cells = {(c["arm"], c["condition"], c["delta_ms"]): c for c in res["cells"]}
    # A0 has no gate re-read: a write released 1 ms before gate end executes invalidly almost always
    c = cells[("A0_native_uid", "async_scope", -1.0)]
    assert c["primary"]["p_hat"] > 0.9
    # fence: every -2 ms trial in A1 fails closed -> PRIMARY counts them as non-events (lead decision,
    # plan v1.1); the S2 sensitivity (exclude fail-closed) is undefined
    c = cells[("A1_dcji", "async_scope", -2.0)]
    assert c["primary"]["n_fail_closed"] == 39 and c["primary"]["n_primary_denominator"] == 39
    assert c["primary"]["p_hat"] == 0.0 and c["quality_flag"] == ""
    s = c["primary"]["sensitivity_exclude_failclosed"]
    assert s["n_fail_closed_excluded"] == 39 and s["p_hat"] is None
    assert ("A1_dcji", "async_scope", -2.0) not in {(x["arm"], x["condition"], x["delta_ms"]) for x in res["stage2_topup"]}
    # late writes never invalidate
    for arm in ("A0_native_uid", "A1_dcji", "A2_fullrefresh", "A3_dcji_webhook"):
        assert cells[(arm, "async_scope", 50.0)]["primary"]["k_invalid_executed"] == 0
    # Wilson bounds bracket p_hat everywhere
    for c in res["cells"]:
        p = c["primary"]
        if p["p_hat"] is not None:
            assert p["wilson_lo"] <= p["p_hat"] <= p["wilson_hi"]
    ew = {(e["arm"], e["condition"]): e for e in res["exposure_window"]}
    assert len(ew) == 12
    e = ew[("A3_dcji_webhook", "async_scope")]
    assert e["bootstrap"]["reps"] == 200 and sum(e["bootstrap"]["distribution"].values()) == 200
    wl = res["webhook_latency_cost"]
    assert wl["hodges_lehmann_ms"] > 0          # webhook adds latency in the simulator
    assert wl["bootstrap"]["hl_ci95_lo_ms"] <= wl["hodges_lehmann_ms"] <= wl["bootstrap"]["hl_ci95_hi_ms"]
    av = {a["arm"]: a for a in res["availability_cost"]}
    assert av["A3_dcji_webhook"]["k_false_abort"] == 2 and av["A1_dcji"]["k_false_abort"] == 0
    assert res["oracle_agreement"]["n_both_classified"] > 0
    uid = {u["arm"]: u for u in res["uid_rebind_control"]}
    assert all(uid[a]["counts"]["UID_BLOCKED"] == 10 for a in uid)


def test_pod_events_input_used_and_validated(tmp_path):
    """plan v1.2: pod_events.jsonl is an optional input; when present it is hashed, parsed strictly
    (unparseable lines -> REFUSED) and used only as the deletion-RV fallback."""
    root = tmp_path / "pe"
    synth.generate_run(root, design="mini", run_id="synthrun1", edge_cases=False)
    rd = root / "results" / "synthrun1"
    trials = load_jsonl(rd / "RAW_TRIALS.jsonl")[0]
    audit = load_jsonl(rd / "audit.log")[0]
    # simulate the running-Pod race in one accepted async trial: DELETE response without resourceVersion
    victim = next(t for t in trials if t["condition"] == "async_scope" and t["delete"]["http_status"] == 200)
    d_rv = victim["delete"]["deleted_object_resource_version"]
    victim["delete"]["deleted_object_resource_version"] = None
    for e in audit:
        if e.get("auditID") == victim["delete"]["audit_id"] and e.get("stage") == "ResponseComplete":
            del e["responseObject"]["metadata"]["resourceVersion"]
    (rd / "RAW_TRIALS.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trials))
    (rd / "audit.log").write_text("".join(json.dumps(e) + "\n" for e in audit))
    pe = [{"schema": "e38v2.podevent.v1", "seq": 1, "type": "DELETED", "uid": victim["delete"]["expected_uid"],
           "rv": d_rv}]
    (rd / "pod_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in pe))
    assert run_analysis(root, tmp_path / "o1", 1, extra=["--design-check", "off"]) == 0
    res = json.loads((tmp_path / "o1" / "results_E38v2.json").read_text())
    assert res["pod_events"]["supplied"] and res["pod_events"]["n_trials_delete_rv_from_pod_watch"] == 1
    assert any(k.endswith("pod_events.jsonl") for k in res["inputs_sha256"])
    with open(tmp_path / "o1" / "oracle_trials_E38v2.csv") as fh:
        row = [r for r in csv.DictReader(fh) if int(r["trial_id"]) == victim["trial_id"]][0]
    assert row["delete_rv_source"] == "pod_watch_deleted" and row["primary_class"] != "UNKNOWN"
    # without the file the same trial is UNKNOWN (pre-registered behaviour)
    (rd / "pod_events.jsonl").rename(rd / "pod_events.off")
    assert run_analysis(root, tmp_path / "o2", 1, extra=["--design-check", "off"]) == 0
    with open(tmp_path / "o2" / "oracle_trials_E38v2.csv") as fh:
        row = [r for r in csv.DictReader(fh) if int(r["trial_id"]) == victim["trial_id"]][0]
    assert row["primary_class"] == "UNKNOWN" and row["primary_reason"] == "delete_rv_missing"
    # corrupt pod-event file -> refusal
    (rd / "pod_events.jsonl").write_text("{not json\n")
    assert run_analysis(root, tmp_path / "o3", 1, extra=["--design-check", "off"]) == 2


def test_full_bootstrap_is_seeded(full_run, tmp_path):
    root, info, out1, rc = full_run
    assert run_analysis(root, tmp_path / "again", 1) == 0
    a = json.loads((out1 / "results_E38v2.json").read_text())
    b = json.loads((tmp_path / "again" / "results_E38v2.json").read_text())
    assert a["exposure_window"] == b["exposure_window"]
    assert a["webhook_latency_cost"] == b["webhook_latency_cost"]
    assert (out1 / "cells_E38v2.csv").read_text() == (tmp_path / "again" / "cells_E38v2.csv").read_text()


def test_stage2_cycle_and_refusal(full_run, tmp_path):
    root0, info, out1, rc = full_run
    root = tmp_path / "s2"
    shutil.copytree(root0 / "results", root / "results")
    shutil.copytree(root0 / "protocol", root / "protocol")
    s2out = tmp_path / "s2list"
    assert s2.main(["--results", str(out1 / "results_E38v2.json"), "--out", str(s2out)]) == 0
    lst = json.loads((s2out / "stage2_cells_E38v2.json").read_text())
    res1 = json.loads((out1 / "results_E38v2.json").read_text())
    assert lst["cells"] == res1["stage2_topup"]
    assert lst["n_cells"] > 0 and all(c["n_topup"] == 54 for c in lst["cells"])
    assert all(0.05 < c["p_hat_stage1"] < 0.95 for c in lst["cells"])
    # fully fenced cell: primary p_hat = 0 (fail-closed = non-event) -> neither topped up nor undefined
    fenced = {"arm": "A1_dcji", "condition": "async_scope", "delta_ms": -2.0}
    assert fenced not in lst["undefined_cells"]
    assert (fenced["arm"], fenced["condition"], fenced["delta_ms"]) not in \
        {(c["arm"], c["condition"], c["delta_ms"]) for c in lst["cells"]}
    sched2, n2 = synth.append_stage2(root, "synthrun1", s2out / "stage2_cells_E38v2.json")
    rd = root / "results" / "synthrun1"
    scheds = [rd / "SCHEDULE.json", sched2]
    out2 = tmp_path / "out2"
    assert run_analysis(root, out2, 2, scheds) == 0
    res2 = json.loads((out2 / "results_E38v2.json").read_text())
    assert res2["completeness_gate"]["n_observed"] == 5812 + n2
    for c in res2["cells"]:
        if (c["arm"], c["condition"], c["delta_ms"]) in {(x["arm"], x["condition"], x["delta_ms"]) for x in lst["cells"]}:
            assert c["n_planned"] == 93 and c["n_observed"] == 93
    # stage-1 analysis of the same (now 2-stage) RAW still works and ignores stage-2 records
    assert run_analysis(root, tmp_path / "out1b", 1) == 0
    # a stage-2 schedule that deviates from the pre-registered top-up rule is refused
    doc = json.loads(sched2.read_text())
    dropped = doc["trials"].pop()
    sched2.write_text(json.dumps(doc))
    rewrite_jsonl(rd / "RAW_TRIALS.jsonl", lambda rs: [r for r in rs if r["trial_id"] != dropped["trial_id"]])
    out3 = tmp_path / "out3"
    assert run_analysis(root, out3, 2, scheds) == 2
    ref = json.loads((out3 / "REFUSED_E38v2.json").read_text())
    assert any("stage-2 schedule mismatch" in r for r in ref["reasons"])
    assert not (out3 / "results_E38v2.json").exists()


def test_design_mismatch_refused_then_pilot_mode(full_run, tmp_path):
    root0, *_ = full_run
    root = tmp_path / "dm"
    shutil.copytree(root0 / "results", root / "results")
    shutil.copytree(root0 / "protocol", root / "protocol")
    rd = root / "results" / "synthrun1"
    doc = json.loads((rd / "SCHEDULE.json").read_text())
    dropped = doc["trials"].pop(0)
    (rd / "SCHEDULE.json").write_text(json.dumps(doc))
    rewrite_jsonl(rd / "RAW_TRIALS.jsonl", lambda rs: [r for r in rs if r["trial_id"] != dropped["trial_id"]])
    relock(root)
    assert run_analysis(root, tmp_path / "o1", 1) == 2
    ref = json.loads((tmp_path / "o1" / "REFUSED_E38v2.json").read_text())
    assert any("stage-1 design mismatch" in r for r in ref["reasons"])
    assert run_analysis(root, tmp_path / "o2", 1, extra=["--design-check", "off"]) == 0
    res = json.loads((tmp_path / "o2" / "results_E38v2.json").read_text())
    assert res["confirmatory"] is False


# ----------------------------------------------------------------------------- refusals on a mini run
@pytest.fixture()
def mini(tmp_path):
    root = tmp_path / "mini"
    synth.generate_run(root, design="mini")
    return root


def _refused(root, tmp_path, *extra):
    out = tmp_path / "out"
    rc = run_analysis(root, out, 1, extra=["--design-check", "off", *extra])
    reasons = json.loads((out / "REFUSED_E38v2.json").read_text())["reasons"] if rc == 2 else []
    return rc, reasons, out


def test_mini_ok(mini, tmp_path):
    rc, _, out = _refused(mini, tmp_path)
    assert rc == 0 and (out / "results_E38v2.json").exists()


def test_refuse_missing_trial(mini, tmp_path):
    rewrite_jsonl(mini / "results/synthrun1/RAW_TRIALS.jsonl", lambda rs: rs[:-1])
    rc, reasons, out = _refused(mini, tmp_path)
    assert rc == 2 and any("planned trials missing" in r for r in reasons)
    assert not (out / "results_E38v2.json").exists()


def test_refuse_duplicate_trial(mini, tmp_path):
    rewrite_jsonl(mini / "results/synthrun1/RAW_TRIALS.jsonl", lambda rs: rs + [rs[3]])
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("duplicate RAW trial_ids" in r for r in reasons)


def test_refuse_seed_mismatch_in_raw(mini, tmp_path):
    def f(rs):
        rs[5]["schedule_seed"] = 1
        return rs
    rewrite_jsonl(mini / "results/synthrun1/RAW_TRIALS.jsonl", f)
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("schedule_seed" in r for r in reasons)


def test_refuse_arm_mismatch_vs_schedule(mini, tmp_path):
    def f(rs):
        rs[7]["arm"] = "A2_fullrefresh" if rs[7]["arm"] != "A2_fullrefresh" else "A1_dcji"
        return rs
    rewrite_jsonl(mini / "results/synthrun1/RAW_TRIALS.jsonl", f)
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("RAW/SCHEDULE mismatches" in r for r in reasons)


def test_refuse_lock_hash_mismatch(mini, tmp_path):
    (mini / "protocol" / "PROTOCOL_E38v2.txt").write_text("edited after lock\n")
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("LOCK hash mismatch: protocol/PROTOCOL_E38v2.txt" in r for r in reasons)


def test_refuse_schedule_edited_after_lock(mini, tmp_path):
    p = mini / "results/synthrun1/SCHEDULE.json"
    p.write_text(p.read_text() + " ")
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("schedule_sha256" in r for r in reasons)


def test_refuse_unlocked_and_forbidden_seed(mini, tmp_path):
    relock(mini, status="NOT_LOCKED_NOT_EXECUTED", schedule_seed=20260912)
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2
    assert any("not LOCKED" in r for r in reasons)
    assert any("forbidden" in r for r in reasons)


def test_refuse_lock_without_hashes(mini, tmp_path):
    lp = mini / "protocol" / "LOCK_E38v2.json"
    lock = json.loads(lp.read_text())
    lp.write_text(json.dumps({"status": "LOCKED", "schedule_seed": lock["schedule_seed"]}))
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("no verifiable hashes" in r for r in reasons)


def test_refuse_truncated_audit(mini, tmp_path):
    p = mini / "results/synthrun1/audit.log"
    txt = p.read_text()
    p.write_text(txt + txt.splitlines()[0][:50] + "\n")
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("audit.log has 1 unparseable" in r for r in reasons)


def test_refuse_missing_input(mini, tmp_path):
    (mini / "results/synthrun1/webhook_log.jsonl").unlink()
    rc, reasons, _ = _refused(mini, tmp_path)
    assert rc == 2 and any("input file missing" in r for r in reasons)


def test_refusal_removes_stale_results(mini, tmp_path):
    rc, _, out = _refused(mini, tmp_path)
    assert rc == 0 and (out / "results_E38v2.json").exists()
    rewrite_jsonl(mini / "results/synthrun1/RAW_TRIALS.jsonl", lambda rs: rs[1:])
    rc, _, out = _refused(mini, tmp_path)
    assert rc == 2 and not (out / "results_E38v2.json").exists()


def test_stage2_topup_refuses_stage2_results(mini, tmp_path):
    rc, _, out = _refused(mini, tmp_path)
    res = json.loads((out / "results_E38v2.json").read_text())
    res["stage_analyzed"] = 2
    (tmp_path / "r.json").write_text(json.dumps(res))
    assert s2.main(["--results", str(tmp_path / "r.json"), "--out", str(tmp_path / "x")]) == 2


def test_cli_entry_points(mini, tmp_path):
    rd = mini / "results/synthrun1"
    r = subprocess.run([sys.executable, str(ROOT / "oracle.py"), "--raw", str(rd / "RAW_TRIALS.jsonl"),
                        "--webhook", str(rd / "webhook_log.jsonl"), "--audit", str(rd / "audit.log"),
                        "--out", str(tmp_path / "o.csv")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, str(ROOT / "analyze_e38v2.py"), "--run-dir", str(rd),
                        "--lock", str(mini / "protocol/LOCK_E38v2.json"), "--stage", "1",
                        "--out", str(tmp_path / "cli"), "--bootstrap-reps", REPS], capture_output=True, text=True)
    assert r.returncode == 2 and "stage-1 design mismatch" in r.stderr     # mini design != pre-registered design
