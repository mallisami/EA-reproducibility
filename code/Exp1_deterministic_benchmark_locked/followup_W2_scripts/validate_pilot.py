"""
Validates the v9 extended-study pilot templates against the LOCKED package's own
scenario.schema.json (read-only, never modified) and the same structural/oracle
integrity checks as deterministic/validate_all_scenarios.py, EXCLUDING that
script's hardcoded ==30 / dev==6/heldout==24 template-count assertions (which
enforce the closed v8.1.1 controlled set and are correctly not applicable to a
deliberately separate, additional v9 extension batch).
"""
import json, itertools
from pathlib import Path
import jsonschema
from oracle import ep, ee, world

PKG_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic" / "schemas"
PILOT_DIR = PKG_ROOT / "03_extended_study_v9" / "scenarios" / "pilot"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def xp(e):
    if isinstance(e, bool): return set()
    if "predicate" in e: return {e["predicate"]}
    if "all" in e:
        z = set()
        for x in e["all"]: z |= xp(x)
        return z
    if "any" in e:
        z = set()
        for x in e["any"]: z |= xp(x)
        return z
    if "not" in e: return xp(e["not"])
    return set()


def main():
    ss = load(SCHEMA_DIR / "scenario.schema.json")
    sts = load(SCHEMA_DIR / "state.schema.json")
    ts = load(SCHEMA_DIR / "transition.schema.json")
    files = sorted(PILOT_DIR.glob("*.json"))
    fail = []
    ids = []
    for path in files:
        s = load(path)
        sid = s["scenario_id"]
        ids.append(sid)
        try:
            jsonschema.validate(s, ss)
            jsonschema.validate(s["initial_state"], sts)
            for t in s["transitions"]:
                jsonschema.validate(t, ts)
        except Exception as e:
            fail.append(f"{sid}: schema {e}")
            continue

        tids = [t["transition_id"] for t in s["transitions"]]
        if len(tids) != len(set(tids)):
            fail.append(f"{sid}: duplicate transition ids")

        for t in s["transitions"]:
            if any(e["path"] == "/facts/action_authorized" for e in t["effects"]):
                fail.append(f"{sid}: authorization mutation")

        material = set()
        for prof, p in s["policy_profiles"].items():
            material |= xp(p["justification"]) | xp(p["binding"]) | xp(p["scope"])
        material |= xp(s["action"]["execution_precondition"])
        material.discard(s["action"]["authorization"]["predicate"])
        rm = s["refresh_model"]
        for pid in material:
            qid = rm["predicate_query_ids"].get(pid)
            if qid is None or qid not in rm["query_costs"] or qid not in rm["query_observed_at_s"]:
                fail.append(f"{sid}: incomplete query mapping for {pid}")

        for wn, w in s["worlds"].items():
            st, done = world(s, w)
            out = {}
            for prof, p in s["policy_profiles"].items():
                auth = ep(s["action"]["authorization"]["predicate"], s, st)
                valid = auth and ee(p["justification"], s, st) and ee(p["binding"], s, st) and ee(p["scope"], s, st)
                out[prof] = "EXECUTE" if valid else "ABORT_REPLAN"
            if out != w["expected_commit_by_policy"]:
                fail.append(f"{sid} {wn}: oracle drift")
            if out["balanced"] != w["expected_commit"]:
                fail.append(f"{sid} {wn}: primary drift")
            full = done == w["transition_path"]
            if wn in ("W2", "W3") and full != w["expected_attack_path_completed"]:
                fail.append(f"{sid} {wn}: path completion mismatch")
            if s["scenario_role"] == "positive_ea_toctou" and wn == "W2":
                exec_ok = ee(s["action"]["execution_precondition"], s, st)
                if out["balanced"] != "ABORT_REPLAN" or not full or not exec_ok:
                    fail.append(f"{sid}: invalid positive W2")
            if wn == "W3" and out["balanced"] != "EXECUTE":
                fail.append(f"{sid}: invalid W3")

        print(f"PASS {sid}: pilot structural/oracle validation")

    if len(set(ids)) != len(ids):
        fail.append("duplicate scenario IDs")

    if fail:
        print("\nFAILURES")
        for x in fail:
            print(" -", x)
        raise SystemExit(1)
    print(f"\nALL {len(ids)} PILOT TEMPLATES PASSED STRUCTURAL/ORACLE VALIDATION.")


if __name__ == "__main__":
    main()
