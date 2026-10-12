#!/usr/bin/env python3
"""
E27 -- Certificate-size characterization under partial threat-model symmetry.

Tests, against the real unmodified locked planner (no live infrastructure
needed), the proposed general relationship:

  PROPOSITION: For a policy that is a disjunction of k independent 2-literal
  conjunctive clauses (F5's OR of AND-pairs, k=3), let m (0 <= m <= k) be the
  number of clauses in which the attacker model allows EITHER literal to be
  independently invalidated ("symmetric" clauses), and k-m the number of
  clauses in which only one fixed literal is ever attacker-reachable
  ("asymmetric" clauses, as in E21's restricted model). Then DCJI's minimum
  certificate size is exactly (k - m)*1 + m*2 = k + m, and the cost
  reduction relative to FullRefresh's fixed 2k-observation certificate is
  (2k - (k+m)) / 2k = (k-m) / (2k).

E21 (m=0) and E25 (m=k=3) are the two extreme corollaries of this general
statement (predicting certificate sizes 3 and 6 respectively, both already
empirically confirmed live). This script constructs the two INTERMEDIATE
cases (m=1, m=2) that were never previously tested, generates the
corresponding scenario programmatically (never hand-edited), and checks
the real planner's output against the closed-form prediction.
"""
import copy, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates

PAIRS = [("flag_a", "flag_b"), ("flag_c", "flag_d"), ("flag_e", "flag_f")]
K = len(PAIRS)

BASE_SCENARIO = {
    "scenario_id": "F5.SYMMETRY.TEST",
    "title": "Programmatic symmetry-characterization scenario (deterministic-only)",
    "family": "F5",
    "realism_tier": "R1",
    "scenario_role": "positive_ea_toctou",
    "development": False,
    "action": {
        "action_type": "quarantine_workload",
        "target_reference": "pod/flashir-symmetry-test",
        "target_snapshot": {"uid": "LIVE"},
        "queue_semantics": "single_exactly_once_attempt",
        "execution_precondition": True,
        "authorization": {"predicate": "p_authorized", "must_remain_valid": True},
    },
    "attacker_capability_tier": "C2",
    "initial_state": {
        "timestamp_s": 0,
        "facts": {
            "flag_a": True, "flag_b": True, "flag_c": True,
            "flag_d": True, "flag_e": True, "flag_f": True,
            "action_authorized": True,
        },
        "resources": [], "processes": [], "network_bindings": [],
        "sessions": [], "context": {}, "evidence": [], "queue": [],
    },
    "predicates": {
        f: {"path": f"/facts/{f}", "op": "eq", "value": True, "evidence_class": "L",
            "description": f"Physical resource {f} valid=true."}
        for f in ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]
    } | {
        "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True,
                          "evidence_class": "DERIVED", "description": "Authorization."},
    },
    "policy_profiles": {
        "balanced": {
            "justification": {
                "any": [{"all": [{"predicate": a}, {"predicate": b}]} for (a, b) in PAIRS]
            },
            "binding": True,
            "scope": True,
        }
    },
    "primary_policy": "balanced",
    "justification_bases": [[a, b] for (a, b) in PAIRS],
    "severity": "S2",
    "notes": "Generated programmatically by verify_symmetry_theorem.py; not hand-authored.",
    "parameterizations": [], "native_controls": [],
    "refresh_model": {
        "cost_unit": "normalized_query_unit",
        "query_costs": {f"query:/facts/{f}": 1.0 for f in
                         ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]},
        "predicate_query_ids": {f: f"query:/facts/{f}" for f in
                                 ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]},
        "query_observed_at_s": {f"query:/facts/{f}": 0.0 for f in
                                 ["flag_a", "flag_b", "flag_c", "flag_d", "flag_e", "flag_f"]},
    },
}


def make_transitions(m):
    """m = number of symmetric clauses (both literals cuttable); the
    remaining k-m clauses get only their FIRST literal as cuttable
    (matching E21's restricted-model convention of always cutting the
    first-named member of each pair)."""
    transitions = []
    for i, (a, b) in enumerate(PAIRS):
        cuttable = [a, b] if i < m else [a]
        for flag in cuttable:
            transitions.append({
                "transition_id": f"cut_{flag}",
                "title": f"Attacker invalidates {flag}",
                "actor": "attacker", "capability_tier": "C2",
                "duration_s": 0.2, "cost_ops": 1, "preconditions": True,
                "effects": [{"op": "set", "path": f"/facts/{flag}", "value": False}],
                "notes": "", "adversarially_induced": True, "causal_parent_ids": [],
            })
    return transitions


def build_scenario(m):
    scenario = copy.deepcopy(BASE_SCENARIO)
    scenario["scenario_id"] = f"F5.SYMMETRY.TEST.m{m}"
    scenario["transitions"] = make_transitions(m)
    return scenario


def main():
    results = []
    for m in range(0, K + 1):
        scenario = build_scenario(m)
        plan = derive_critical_predicates(scenario, 5.0, "C2", "balanced")
        predicted_size = K + m
        predicted_reduction = (K - m) / (2 * K)
        actual_size = len(plan["critical_predicates"])
        actual_reduction = (2 * K - actual_size) / (2 * K)
        row = {
            "m_symmetric_clauses": m,
            "k_total_clauses": K,
            "predicted_certificate_size": predicted_size,
            "actual_certificate_size": actual_size,
            "prediction_matches": predicted_size == actual_size,
            "predicted_reduction_fraction": predicted_reduction,
            "actual_reduction_fraction": actual_reduction,
            "planner_certificate": sorted(plan["critical_predicates"]),
            "planner_cost_units": plan["query_cost_units"],
            "goal_count": plan["goal_count"],
        }
        results.append(row)
        print(f"m={m}: predicted_size={predicted_size} actual_size={actual_size} "
              f"match={row['prediction_matches']} certificate={row['planner_certificate']}")

    all_match = all(r["prediction_matches"] for r in results)
    out = {
        "theorem": "certificate_size = k + m, reduction_fraction = (k-m)/(2k)",
        "k": K,
        "all_predictions_confirmed": all_match,
        "results": results,
    }
    (ROOT / "results" / "symmetry_verification.json").write_text(json.dumps(out, indent=2))
    print("\nALL PREDICTIONS CONFIRMED:", all_match)
    if not all_match:
        raise SystemExit("THEOREM PREDICTION FAILED -- do not write this into the manuscript as proven.")


if __name__ == "__main__":
    main()
