#!/usr/bin/env python3
"""Faithful mirror of inputs/frozen/03_extended_study_v9/scripts/run_pilot_confirmatory.py
with ONLY two changes: (1) the flashir_core package is imported from $DCJI_CORE_ROOT
(deterministic_orig or deterministic_patched copy), (2) output goes to $DCJI_OUT_FILE
instead of the frozen results directory. Calls/arguments are byte-for-byte the same.
"""
import sys, json, os
from pathlib import Path

sys.path.insert(0, os.environ["DCJI_CORE_ROOT"])
from flashir_core.engine import replay_path
from flashir_core.expr import commit_components
from flashir_core.defenses import B0_oneshot, B4_fullrefresh, B6_dcji_gate

PILOT_DIR = Path(os.environ["DCJI_PILOT_DIR"])
OUT_FILE = Path(os.environ["DCJI_OUT_FILE"])


def main():
    rows = []
    for path in sorted(PILOT_DIR.glob("*.json")):
        s = json.loads(path.read_text())
        profile = s["primary_policy"]
        initial = s["initial_state"]
        w = s["worlds"]["W2"]
        rep = replay_path(s, w["transition_path"], w["commit_deadline_s"])
        cs = rep["state"]
        comp = commit_components(s, cs, profile)

        b0 = B0_oneshot(s, initial, cs, profile)
        b4 = B4_fullrefresh(s, initial, cs, profile)
        b6 = B6_dcji_gate(s, initial, cs, profile, deadline_s=w["commit_deadline_s"],
                          attacker_capability=s["attacker_capability_tier"],
                          max_expansions=100000, max_subset_evals=100000)

        def unsafe(d):
            return d["decision"] == "EXECUTE" and comp["authorization"] and comp["executable"] and not comp["valid"]

        rows.append({
            "scenario_id": s["scenario_id"],
            "family": s["family"],
            "attacker_capability_tier": s["attacker_capability_tier"],
            "deadline_s": w["commit_deadline_s"],
            "B0_decision": b0["decision"], "B0_unsafe": unsafe(b0),
            "B4_decision": b4["decision"], "B4_unsafe": unsafe(b4), "B4_query_cost": b4["query_cost_units"],
            "B6_decision": b6["decision"], "B6_unsafe": unsafe(b6), "B6_query_cost": b6["query_cost_units"],
            "B6_defense_mode": b6.get("defense_mode"),
            # extra (not in frozen file; stripped before comparison): certificate identity
            "_B6_critical_query_ids": b6.get("critical_query_ids"),
            "_B6_goal_count": b6.get("attack_goal_state_count"),
        })
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(rows, indent=2))
    n = len(rows)
    print(f"n={n} B4_unsafe={sum(r['B4_unsafe'] for r in rows)} B6_unsafe={sum(r['B6_unsafe'] for r in rows)} "
          f"mean_B4_cost={sum(r['B4_query_cost'] for r in rows)/n:.3f} mean_B6_cost={sum(r['B6_query_cost'] for r in rows)/n:.3f}")


if __name__ == "__main__":
    main()
