"""Independent recomputation (A, B, E, F, G) agrees with the main analysis and is truly independent."""
import json
import re
from pathlib import Path

import recompute_independent_e39 as ind

SRC = Path(ind.__file__).read_text()


def test_independent_script_imports_no_analysis_code():
    imports = re.findall(r"^\s*(?:from|import)\s+([\w\.]+)", SRC, re.M)
    assert not {"oracle_e39", "e39_common", "analyze_e39", "numpy", "scipy", "pandas"} & set(imports)


def test_independent_equals_main_on_mini_run(mini_run, mini_results):
    res, out = mini_results
    cells = ind.recompute(mini_run["raws"], mini_run["audit"])
    assert ind.compare(cells, res) == []
    assert {k[0] for k in cells} == {"A", "B", "E", "F", "G"}


def test_independent_cli_compare_exit_codes(mini_run, mini_results, tmp_path):
    res, out = mini_results
    args = ["--raw", str(mini_run["run"] / "RAW_TRIALS.jsonl"), "--audit", str(mini_run["run"] / "audit.log"),
            "--schedule", str(mini_run["run"] / "SCHEDULE.json"), "--compare", str(out / "results_E39.json"),
            "--out", str(tmp_path / "ind.json")]
    assert ind.main(args) == 0
    doc = json.loads((tmp_path / "ind.json").read_text())
    assert doc["comparison"]["n_differences"] == 0
    tampered = json.loads((out / "results_E39.json").read_text())
    for c in tampered["cells"]:
        if c["cell"][0] == "G":
            c["primary"]["k_invalid_executed"] += 1
            break
    (tmp_path / "t.json").write_text(json.dumps(tampered))
    args[args.index("--compare") + 1] = str(tmp_path / "t.json")
    assert ind.main(args) == 3


def test_independent_handles_fixture_cut_and_a4d_404():
    import e39_testkit as kit
    r, t, raw, audit, wh = kit.one("G", "A1", "out_of_model_cut_bdf")
    raw["writer_arrivals"] = None
    for o in ("evidence-b", "evidence-d", "evidence-f"):
        raw["fixture"]["initial_values"][o] = "false"
    assert ind.classify(raw, ind.audit_rc(audit)) == ("event", "cut_in_fixture")
    r, t, raw, audit, wh = kit.one("A", "A4d", "async_scope", delta=50.0)
    assert ind.classify(raw, ind.audit_rc(audit))[0] == "nonevent"


def test_independent_excludes_rv_conflicts():
    import e39_testkit as kit
    r, t, raw, audit, wh = kit.one("A", "A1", "async_scope", delta=0.0, P={"d_commit": 5.0})
    raw["writer"]["resulting_resource_version"] = "1"
    assert ind.classify(raw, ind.audit_rc(audit)) == ("excluded", "write_rv")
