#!/usr/bin/env python3
"""
E30 -- General certificate-size theorem for arbitrary-size clauses.

Generalizes E27's Proposition 3 (proved and verified only for k clauses of
exactly 2 literals each) to clauses of ARBITRARY size, with symmetry
assigned per-clause independently (not just "first m clauses symmetric").

GENERAL PROPOSITION: For a policy that is a disjunction of k independent
conjunctive clauses, where clause i has s_i literals, let SYM be the
subset of clauses in which the attacker model allows ANY single literal
to be independently invalidated ("symmetric"), with the remaining clauses
having exactly one fixed, designated literal that is attacker-reachable
("asymmetric"). Then DCJI's minimum certificate size is exactly:

    sum_{i not in SYM} 1  +  sum_{i in SYM} s_i

i.e. one query per asymmetric clause regardless of its size, and ALL s_i
queries for a symmetric clause. E27's Proposition 3 is the special case
s_i=2 for all i.

This script tests the general formula against the real, unmodified
planner across MIXED clause sizes (2 and 3) and MIXED, non-contiguous
symmetry assignments (not just "first m clauses"), which E27 did not
test.
"""
import copy, itertools, json, string, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT.parents[1]
sys.path.insert(0, str(PKG_ROOT / "02_locked_methodology_v8.1.1" / "deterministic"))
from flashir_core.dcji import derive_critical_predicates


def build_scenario(clause_sizes, symmetric_flags):
    """clause_sizes: list of ints (size of each clause).
    symmetric_flags: list of bools, same length, True = symmetric clause."""
    assert len(clause_sizes) == len(symmetric_flags)
    letters = iter(string.ascii_lowercase)
    clauses = []  # list of list of literal names
    for size in clause_sizes:
        clauses.append([f"flag_{next(letters)}" for _ in range(size)])
    all_flags = [f for clause in clauses for f in clause]

    scenario = {
        "scenario_id": "GENERAL.SYMMETRY.TEST",
        "title": "Programmatic general-clause-size symmetry test (deterministic-only)",
        "family": "F5", "realism_tier": "R1", "scenario_role": "positive_ea_toctou",
        "development": False,
        "action": {
            "action_type": "quarantine_workload",
            "target_reference": "pod/flashir-general-symmetry-test",
            "target_snapshot": {"uid": "LIVE"},
            "queue_semantics": "single_exactly_once_attempt",
            "execution_precondition": True,
            "authorization": {"predicate": "p_authorized", "must_remain_valid": True},
        },
        "attacker_capability_tier": "C2",
        "initial_state": {
            "timestamp_s": 0,
            "facts": {f: True for f in all_flags} | {"action_authorized": True},
            "resources": [], "processes": [], "network_bindings": [],
            "sessions": [], "context": {}, "evidence": [], "queue": [],
        },
        "predicates": {
            f: {"path": f"/facts/{f}", "op": "eq", "value": True, "evidence_class": "L",
                "description": f"Physical resource {f} valid=true."}
            for f in all_flags
        } | {
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True,
                              "evidence_class": "DERIVED", "description": "Authorization."},
        },
        "policy_profiles": {
            "balanced": {
                "justification": {
                    "any": [{"all": [{"predicate": f} for f in clause]} for clause in clauses]
                },
                "binding": True, "scope": True,
            }
        },
        "primary_policy": "balanced",
        "justification_bases": clauses,
        "severity": "S2",
        "notes": "Generated programmatically by verify_general_theorem.py; not hand-authored.",
        "parameterizations": [], "native_controls": [],
        "refresh_model": {
            "cost_unit": "normalized_query_unit",
            "query_costs": {f"query:/facts/{f}": 1.0 for f in all_flags},
            "predicate_query_ids": {f: f"query:/facts/{f}" for f in all_flags},
            "query_observed_at_s": {f"query:/facts/{f}": 0.0 for f in all_flags},
        },
        "transitions": [],
    }

    for clause, symmetric in zip(clauses, symmetric_flags):
        cuttable = clause if symmetric else clause[:1]
        for flag in cuttable:
            scenario["transitions"].append({
                "transition_id": f"cut_{flag}", "title": f"Attacker invalidates {flag}",
                "actor": "attacker", "capability_tier": "C2",
                "duration_s": 0.2, "cost_ops": 1, "preconditions": True,
                "effects": [{"op": "set", "path": f"/facts/{flag}", "value": False}],
                "notes": "", "adversarially_induced": True, "causal_parent_ids": [],
            })
    return scenario


def predicted_certificate_size(clause_sizes, symmetric_flags):
    return sum(s if sym else 1 for s, sym in zip(clause_sizes, symmetric_flags))


TEST_CASES = [
    # (clause_sizes, symmetric_flags, label)
    ([2, 2, 2], [False, False, False], "uniform size-2, all asymmetric (E27 m=0 special case)"),
    ([2, 2, 2], [True, True, True], "uniform size-2, all symmetric (E27 m=3 special case)"),
    ([2, 3, 2], [False, False, False], "mixed sizes 2/3/2, all asymmetric"),
    ([2, 3, 2], [False, True, False], "mixed sizes, ONLY the size-3 clause symmetric"),
    ([2, 3, 2], [True, False, True], "mixed sizes, the two size-2 clauses symmetric, size-3 asymmetric"),
    ([2, 3, 2], [True, True, True], "mixed sizes, all symmetric"),
    ([3, 3, 3], [False, True, False], "uniform size-3, middle clause symmetric only"),
    ([2, 3, 4], [True, False, True], "three DIFFERENT sizes (2,3,4), non-contiguous symmetric set"),
]


def main():
    results = []
    for clause_sizes, symmetric_flags, label in TEST_CASES:
        scenario = build_scenario(clause_sizes, symmetric_flags)
        plan = derive_critical_predicates(scenario, 5.0, "C2", "balanced")
        predicted = predicted_certificate_size(clause_sizes, symmetric_flags)
        actual = len(plan["critical_predicates"])
        row = {
            "label": label,
            "clause_sizes": clause_sizes,
            "symmetric_flags": symmetric_flags,
            "predicted_certificate_size": predicted,
            "actual_certificate_size": actual,
            "prediction_matches": predicted == actual,
            "planner_certificate": sorted(plan["critical_predicates"]),
            "planner_cost_units": plan["query_cost_units"],
            "goal_count": plan["goal_count"],
        }
        results.append(row)
        status = "MATCH" if row["prediction_matches"] else "MISMATCH"
        print(f"[{status}] {label}: predicted={predicted} actual={actual}")

    all_match = all(r["prediction_matches"] for r in results)
    out = {
        "theorem": "certificate_size = sum(1 for asymmetric clauses) + sum(clause_size for symmetric clauses)",
        "generalizes": "E27 Proposition 3 (uniform size-2 clauses) to arbitrary, mixed clause sizes",
        "all_predictions_confirmed": all_match,
        "results": results,
    }
    (ROOT / "results" / "general_theorem_verification.json").write_text(json.dumps(out, indent=2))
    print("\nALL PREDICTIONS CONFIRMED:", all_match)
    if not all_match:
        raise SystemExit("GENERAL THEOREM PREDICTION FAILED -- do not write into manuscript as proven.")


if __name__ == "__main__":
    main()
