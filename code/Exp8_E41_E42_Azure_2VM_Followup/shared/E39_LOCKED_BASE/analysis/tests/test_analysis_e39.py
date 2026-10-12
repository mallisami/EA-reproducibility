"""analyze_e39.py: per-sub-experiment endpoints, completeness gate, strict design check, stage 2 (E39-A)."""
import json
from pathlib import Path

import pytest

import analyze_e39 as an
import e39_testkit as kit
import synth_e39 as syn


# ----------------------------------------------------------------------------- outputs on the mini run
def test_mini_run_writes_all_outputs(mini_results):
    res, out = mini_results
    for f in ("results_E39.json", "cells_E39.csv", "oracle_trials_E39.csv", "exposure_E39.csv", "B_rates_E39.csv",
              "C_latency_E39.csv"):
        assert (out / f).is_file(), f
    assert res["schema"] == "e39.results.v1" and res["confirmatory"] is False
    assert set(res["experiments"]) == set("ABCDEFG")
    assert res["completeness_gate"]["passed"] is True


def test_A_exposure_windows_and_availability(mini_results):
    res, _ = mini_results
    A = res["E39_A"]
    groups = {tuple(e["group"]) for e in A["exposure_window"]}
    assert ("A4m", "async_scope") in groups and ("A1", "async_app_policy") in groups
    for e in A["exposure_window"]:
        assert e["bootstrap"]["reps"] == 200 and e["window_area_ms"] is not None
    av = {a["arm"]: a for a in A["availability_cost"]}
    assert set(av) == {"A1", "A3", "A4d", "A4m"}
    assert av["A1"]["no_write"]["k_409"] == 0
    assert "lag_ms" in A["mirror"] and A["mirror"]["n_with_mirror"] > 0


def test_A_stage2_candidates_only_A_async_cells(mini_results):
    res, _ = mini_results
    for r in res["stage2_topup"]:
        assert r["cell"][0] == "A" and r["condition"] in ("async_scope", "async_app_policy", "async_evidence_last_literal")
        assert 0.05 < r["p_hat_stage1"] < 0.95 and r["n_topup"] == 93 - 3


def test_B_rates_prediction_and_model_based_extrapolation(mini_results):
    res, _ = mini_results
    B = res["E39_B"]
    cells = {(c["arm"], c["lambda_per_s"]): c for c in B["per_cell"]}
    assert len(cells) == 12
    for c in B["per_cell"]:
        assert c["n_responses"] > 0 and 0 <= c["per_1000"] <= 1000
        assert c["per_1000_lo"] <= c["per_1000"] <= c["per_1000_hi"]
    # A2 has no E39-A curve: its window comes from --w-external
    assert cells[("A2", 10.0)]["W_prediction_ms"] == 2.5 and "external" in cells[("A2", 10.0)]["W_prediction_source"]
    assert cells[("A1", 10.0)]["W_prediction_source"].startswith("E39-A")
    assert all(e["label"].startswith("MODEL-BASED") for e in B["extrapolation_model_based"])
    assert {e["lambda_per_s"] for e in B["extrapolation_model_based"]} == {0.01, 0.1}
    # A4d: commit-bound -> no invalid executions under Poisson arrivals
    assert all(cells[("A4d", lam)]["k_invalid_executed"] == 0 for lam in (1.0, 10.0, 100.0))


def test_C_sequential_excess_matches_3c_and_parallel_within_bounds(mini_results):
    res, _ = mini_results
    C = {(c["injected_latency_ms"], c["acquisition_mode"], c["condition"], c["planning_mode"]): c for c in res["E39_C"]["cells"]}
    for lat in (50.0, 200.0, 1000.0):
        seq = C[(lat, "sequential", "stable", "precomputed")]["cost_model"]
        assert seq["refresh_ms_excess_over_c0_ms"] == pytest.approx(3 * lat, rel=0.01)
        assert seq["refresh_ms_excess_within_model_bounds"] is True
        par = C[(lat, "parallel", "stable", "precomputed")]["cost_model"]
        assert par["refresh_ms_excess_within_model_bounds"] is True
        assert 0.31 * lat <= par["refresh_ms_excess_over_c0_ms"] <= 3 * lat


def test_C_paired_hl_has_bootstrap_ci_and_break_even(mini_results):
    res, _ = mini_results
    c = next(c for c in res["E39_C"]["cells"] if c["injected_latency_ms"] == 200.0 and c["acquisition_mode"] == "sequential")
    m = c["gate_ms"]
    assert m["n_pairs"] == 3 and m["boot_ci95_lo"] <= m["hl_A2_minus_A1_ms"] <= m["boot_ci95_hi"]
    be = {(b["acquisition_mode"], b["condition"], b["planning_mode"]): b for b in res["E39_C"]["break_even"]}
    b = be[("sequential", "stable", "per_incident")]
    # synthetic: DCJI planning costs ~6 ms more, saves 3 x (0.3 ms + c) -> break-even ~ (6 - 0.9)/3 ms
    assert b["break_even_c_ms"] == pytest.approx((6.0 - 0.9) / 3.0, abs=0.6)
    assert b["model_break_even_c_ms"] == pytest.approx(2.0, abs=0.5)


def test_D_host_trace_and_throttle_summary(mini_results):
    res, _ = mini_results
    D = res["E39_D"]
    lv = {h["load_level"]: h for h in D["host_trace"]}
    assert set(lv) == {"idle", "moderate", "high"}
    assert lv["high"]["host_cpu_pct"]["median"] > lv["idle"]["host_cpu_pct"]["median"]
    assert {tuple(e["group"])[1] for e in D["exposure_window_by_load"]} == {"idle", "moderate", "high"}
    assert len(D["sensitivity_exclude_throttled"]) == 2 * 3 * 8     # 8 D deltas (grid extended 2026-09-26)


def test_E_curves_and_difference(mini_results):
    res, _ = mini_results
    E = res["E39_E"]
    assert len(E["A3_minus_A1_by_delta"]) == 2 * 8                  # 2 conditions x 8 E deltas
    assert "A4" in E["structural_limitation"]


def test_F_curves_by_implementation_and_latency(mini_results):
    res, _ = mini_results
    F = res["E39_F"]
    assert {tuple(e["group"]) for e in F["exposure_window_by_impl"]} == {("A1", "python"), ("A1", "client-go"), ("A4d", "client-go")}
    comp = F["latency_comparisons"][0]
    assert comp["comparison"].startswith("A1/client-go vs A1/python")
    assert comp["gate_end_to_send_ms"]["hl_ms"] == pytest.approx(-0.03, abs=1e-6)
    assert F["go_client_versions"] == ["v0.37.0"]


def test_G_dcji_vs_fullrefresh(mini_results):
    res, _ = mini_results
    g = {r["condition"]: r for r in res["E39_G"]["by_condition"]}
    oom = g["out_of_model_cut_bdf"]
    assert oom["A1_dcji"]["p_hat"] == 1.0 and oom["A2_fullrefresh"]["p_hat"] == 0.0
    assert oom["dcji_minus_fullrefresh"]["newcombe_lo"] > 0
    assert oom["dcji_minus_fullrefresh_exact_unconditional"]["lo"] > 0
    assert g["in_model_cut_ace"]["A1_dcji"]["p_hat"] == 0.0


def test_cells_csv_has_all_experiments(mini_results):
    _, out = mini_results
    txt = (out / "cells_E39.csv").read_text().splitlines()
    exps = {line.split(",")[0] for line in txt[1:]}
    assert exps == set("ABCDEFG")


# ----------------------------------------------------------------------------- completeness gate
@pytest.fixture()
def small_run(tmp_path):
    return kit.make_run(tmp_path, ["G"], mini=True)


def _refused(paths, out, **kw):
    rc = an.main(kit.analyze_args(paths, out, **kw))
    return rc, (json.loads((out / "REFUSED_E39.json").read_text()) if (out / "REFUSED_E39.json").is_file() else None)


def test_gate_passes_small_run(small_run, tmp_path):
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 0 and ref is None


def test_gate_refuses_missing_trial(small_run, tmp_path):
    kit.rewrite_raw(small_run["run"], small_run["raws"][:-1])
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2 and any("missing from RAW" in r for r in ref["reasons"])


def test_gate_refuses_extra_and_duplicate_trials(small_run, tmp_path):
    extra = dict(small_run["raws"][0], trial_id=999999)
    kit.rewrite_raw(small_run["run"], small_run["raws"] + [small_run["raws"][0], extra])
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2
    assert any("duplicate" in r for r in ref["reasons"]) and any("not in any schedule" in r for r in ref["reasons"])


def test_gate_refuses_unlocked_status(small_run, tmp_path):
    lk = json.loads(Path(small_run["lock"]).read_text())
    lk["status"] = "DRAFT"
    Path(small_run["lock"]).write_text(json.dumps(lk))
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2 and any("not LOCKED" in r for r in ref["reasons"])


def test_gate_refuses_hash_mismatch_of_plan(small_run, tmp_path):
    Path(small_run["plan"]).write_text("edited after lock\n")
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2 and any("hash mismatch" in r and "analysis_plan" in r for r in ref["reasons"])


def test_gate_refuses_forbidden_seed(tmp_path):
    p = kit.make_run(tmp_path, ["G"], mini=True, seed=1772720255)
    rc, ref = _refused(p, tmp_path / "o")
    assert rc == 2 and any("forbidden" in r for r in ref["reasons"])


def test_gate_refuses_raw_schedule_field_mismatch(small_run, tmp_path):
    raws = [dict(r) for r in small_run["raws"]]
    raws[0]["arm"] = "A2_fullrefresh" if raws[0]["arm"] == "A1_dcji" else "A1_dcji"
    kit.rewrite_raw(small_run["run"], raws)
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2 and any("RAW/SCHEDULE mismatches" in r for r in ref["reasons"])


def test_gate_refuses_schema_mismatch(small_run, tmp_path):
    raws = [dict(r) for r in small_run["raws"]]
    raws[0]["schema"] = "e38v2.trial.v1"
    kit.rewrite_raw(small_run["run"], raws)
    rc, ref = _refused(small_run, tmp_path / "o")
    assert rc == 2 and any("schema" in r for r in ref["reasons"])


def test_refusal_removes_stale_results(small_run, tmp_path):
    out = tmp_path / "o"
    assert an.main(kit.analyze_args(small_run, out)) == 0
    assert (out / "results_E39.json").is_file()
    kit.rewrite_raw(small_run["run"], small_run["raws"][:-2])
    assert an.main(kit.analyze_args(small_run, out)) == 2
    assert not (out / "results_E39.json").is_file() and (out / "REFUSED_E39.json").is_file()


def test_unknown_and_infra_trials_do_not_refuse_but_are_counted(small_run, tmp_path):
    raws = [dict(r) for r in small_run["raws"]]
    raws[0]["status"] = "INFRA_ERROR"
    raws[1]["status"] = "UNKNOWN"
    kit.rewrite_raw(small_run["run"], raws)
    out = tmp_path / "o"
    assert an.main(kit.analyze_args(small_run, out)) == 0
    res = json.loads((out / "results_E39.json").read_text())
    assert res["overall_counts_primary"]["INFRA_ERROR"] == 1 and res["overall_counts_primary"]["UNKNOWN"] == 1


# ----------------------------------------------------------------------------- strict design check
def test_strict_design_check_passes_full_E_and_G(tmp_path):
    p = kit.make_run(tmp_path, ["E", "G"], mini=False)
    out = tmp_path / "o"
    assert an.main(kit.analyze_args(p, out, experiments="E,G", design="strict", reps=100)) == 0
    res = json.loads((out / "results_E39.json").read_text())
    assert res["confirmatory"] is True and res["completeness_gate"]["n_planned"] == 1248 + 156


def test_strict_design_check_refuses_mini_design(small_run, tmp_path):
    rc, ref = _refused(small_run, tmp_path / "o", experiments="G", design="strict")
    assert rc == 2 and any("design mismatch" in r for r in ref["reasons"])


def test_strict_design_check_refuses_bad_c_pairs(tmp_path):
    sch = syn.build_schedule(["C"], kit.SEED, mini=False)
    for t in sch["trials"]:
        if t["pair_id"] == 1:
            t["pair_id"] = 2          # pair 2 now has four trials, pair 1 none
    p = kit.make_run(tmp_path, ["C"], schedule=sch)
    rc, ref = _refused(p, tmp_path / "o", experiments="C", design="strict", reps=100)
    assert rc == 2 and any("pair_ids" in r for r in ref["reasons"])


def test_stage2_requires_A(small_run, tmp_path):
    rc, ref = _refused(small_run, tmp_path / "o", experiments="G", stage=2)
    assert rc == 2 and any("E39-A" in r for r in ref["reasons"])


# ----------------------------------------------------------------------------- stage 2 (E39-A only)
def test_stage2_topup_end_to_end_full_A(tmp_path):
    import stage2_topup_e39 as s2
    from e39_common import sha256_file
    p = kit.make_run(tmp_path, ["A"], mini=False)
    o1 = tmp_path / "s1"
    assert an.main(kit.analyze_args(p, o1, experiments="A", design="strict", reps=100)) == 0
    assert s2.main(["--results", str(o1 / "results_E39.json"), "--out", str(tmp_path / "s2")]) == 0
    doc = json.loads((tmp_path / "s2" / "stage2_cells_E39.json").read_text())
    assert doc["n_cells"] >= 1
    # runner side (simulated): build the stage-2 schedule from the top-up list, ids >= 100001
    trials, tid = [], 100000
    for c in doc["cells"]:
        for i in range(c["n_topup"]):
            tid += 1
            trials.append({"trial_id": tid, "stage": 2, "block": 900, "experiment": "A",
                           "arm": syn.RAW_ARM[(c["arm"], "python")], "condition": c["condition"],
                           "delta_ms": c["delta_ms"], "rep": 39 + i, "schedule_seed": kit.SEED,
                           "client_impl": "python", "action_type": "pod_delete"})
    sch2 = {"schema": "e39.schedule.v1", "run_id": p["schedule"]["run_id"], "schedule_seed": kit.SEED, "stage": 2, "trials": trials}
    ctx2 = syn.Ctx(run_id=p["schedule"]["run_id"], seed=kit.SEED + 5)
    ctx2.n = 500000                      # distinct audit ids / uids from stage 1
    raws2, au2, wh2, _ = syn.simulate(sch2, kit.SEED + 5, ctx=ctx2)
    s2p = p["run"] / "SCHEDULE_STAGE2.json"
    s2p.write_text(json.dumps(sch2))
    kit.rewrite_raw(p["run"], p["raws"] + raws2)
    with open(p["run"] / "audit.log", "a") as fh:
        for e in au2:
            fh.write(json.dumps(e) + "\n")
    with open(p["run"] / "webhook_log.jsonl", "a") as fh:
        for e in wh2:
            fh.write(json.dumps(e) + "\n")
    lk = json.loads(Path(p["lock"]).read_text())
    lk["stage2_schedule_sha256"] = sha256_file(s2p)
    Path(p["lock"]).write_text(json.dumps(lk))
    o2 = tmp_path / "o2"
    args = kit.analyze_args(p, o2, "--schedule", str(p["run"] / "SCHEDULE.json"), "--schedule", str(s2p),
                            experiments="A", design="strict", reps=100, stage=2)
    assert an.main(args) == 0
    res = json.loads((o2 / "results_E39.json").read_text())
    assert res["stage2_topup_verified"] and res["completeness_gate"]["n_planned"] == 5344 + len(trials)
    # a stage-2 schedule that deviates from the rule is refused
    sch2["trials"] = sch2["trials"][:-1]
    s2p.write_text(json.dumps(sch2))
    lk["stage2_schedule_sha256"] = sha256_file(s2p)
    Path(p["lock"]).write_text(json.dumps(lk))
    kit.rewrite_raw(p["run"], p["raws"] + raws2[:-1])
    assert an.main(kit.analyze_args(p, tmp_path / "o3", "--schedule", str(p["run"] / "SCHEDULE.json"), "--schedule", str(s2p),
                                    experiments="A", design="strict", reps=100, stage=2)) == 2


# ----------------------------------------------------------------------------- E39-D load guard in the gate
def test_throttled_trials_pass_gate_and_stay_in_nominal_cell(tmp_path):
    p = kit.make_run(tmp_path, ["D"], mini=True, P={"p_throttle": 1.0})
    high = [r for r in p["raws"] if r["requested_level"] == "high"]
    assert high and all(r["load_level"] == "moderate" and r["host"]["load_throttled"] for r in high)
    out = tmp_path / "o"
    assert an.main(kit.analyze_args(p, out, experiments="D")) == 0
    res = json.loads((out / "results_E39.json").read_text())
    lv = {h["load_level"]: h for h in res["E39_D"]["host_trace"]}
    assert lv["high"]["n_throttled"] == len(high) and lv["high"]["effective_levels"] == {"moderate": len(high)}
    assert any(c["cell"][2] == "high" for c in res["cells"])


def test_unthrottled_level_mismatch_is_refused(tmp_path):
    p = kit.make_run(tmp_path, ["D"], mini=True)
    raws = [dict(r) for r in p["raws"]]
    i = next(i for i, r in enumerate(raws) if r["requested_level"] == "high")
    raws[i]["load_level"] = "moderate"
    raws[i]["requested_level"] = None
    raws[i]["host"] = dict(raws[i]["host"], load_throttled=False)
    kit.rewrite_raw(p["run"], raws)
    rc, ref = _refused(p, tmp_path / "o", experiments="D")
    assert rc == 2 and any("load_level" in r for r in ref["reasons"])