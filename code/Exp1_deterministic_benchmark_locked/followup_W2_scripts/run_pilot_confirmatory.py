"""
Runs the locked, UNMODIFIED flashir_core engine/defenses (imported as a library
from the original package path, never edited) against the new v9 pilot templates
at world W2 (the confirmatory ea_toctou world), mirroring exactly the calling
convention used by the locked deterministic/run_heldout_e1_e5.py, to produce
B0/B4/B6 comparison rows for these new templates.
"""
import sys, json, hashlib
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.engine import replay_path
from flashir_core.expr import commit_components
from flashir_core.defenses import B0_oneshot, B4_fullrefresh, B6_dcji_gate

PILOT_DIR = PKG_ROOT / "03_extended_study_v9" / "scenarios" / "pilot"
OUT = PKG_ROOT / "03_extended_study_v9" / "results"
OUT.mkdir(parents=True, exist_ok=True)


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
        })
        print(f"{s['scenario_id']}: B0={b0['decision']}({unsafe(b0)})  "
              f"B4={b4['decision']}({unsafe(b4)},cost={b4['query_cost_units']})  "
              f"B6={b6['decision']}({unsafe(b6)},cost={b6['query_cost_units']},mode={b6.get('defense_mode')})")

    (OUT / "pilot_confirmatory_W2.json").write_text(json.dumps(rows, indent=2))
    n = len(rows)
    b4_cost = sum(r["B4_query_cost"] for r in rows) / n
    b6_cost = sum(r["B6_query_cost"] for r in rows) / n
    discordant = sum(1 for r in rows if r["B6_unsafe"] != r["B4_unsafe"])
    print(f"\nn={n}  B4_unsafe={sum(r['B4_unsafe'] for r in rows)}  B6_unsafe={sum(r['B6_unsafe'] for r in rows)}  "
          f"discordant={discordant}  mean_B4_cost={b4_cost:.3f}  mean_B6_cost={b6_cost:.3f}")


if __name__ == "__main__":
    main()
