"""
Generates a pilot batch of NEW F1-family (object-rebinding) held-out templates
for the v9 extended confirmatory study, following the exact causal/logical
structure already used by the locked F1.2/F1.3 templates (remove_original ->
create_successor under a reusable logical reference), varied across genuinely
different resource kinds, attacker capability tiers, commit deadlines and
severities -- the same kind of variation the locked authors themselves used
between F1.2 (Pod) and F1.3 (Deployment).

Every declared expected_commit_by_policy / expected_attack_path_completed value
is COMPUTED via oracle.py (a behavior-equivalent copy of the locked validator's
own oracle logic), not hand-guessed, so correctness is self-verifying.
"""
import json
from pathlib import Path
from oracle import compute_expected_commit_by_policy

OUT = Path(__file__).resolve().parents[1] / "scenarios" / "pilot"
OUT.mkdir(parents=True, exist_ok=True)

# (scenario_num, resource_kind, target_name, capability_tier, deadline_s, severity, realism_tier)
SPECS = [
    (7,  "DaemonSet",  "log-forwarder",  "C1", 5,  "S1", "R1"),
    (8,  "Job",        "batch-etl",      "C2", 5,  "S2", "R2"),
    (9,  "ReplicaSet", "auth-worker",    "C3", 5,  "S3", "R2"),
    (10, "CronJob",    "nightly-report", "C2", 8,  "S2", "R1"),
    (11, "Pod",        "cache-node",     "C1", 3,  "S1", "R1"),
    (12, "Service",    "billing-svc",    "C3", 10, "S4", "R3"),
    (13, "StatefulSet","session-store",  "C2", 5,  "S2", "R2"),
    (14, "Deployment", "notif-gateway",  "C1", 5,  "S1", "R1"),
]


def build_template(num, kind, name, cap_tier, deadline_s, severity, realism):
    sid_key = f"F1_{num}"
    uid_a = f"X{num}A"
    uid_b = f"X{num}B"
    scenario = {
        "scenario_id": f"F1.{num}",
        "title": f"{kind} Object Recreation ({name})",
        "family": "F1",
        "realism_tier": realism,
        "scenario_role": "positive_ea_toctou",
        "development": False,
        "action": {
            "action_type": "delete_or_stop_workload",
            "target_reference": f"{kind.lower()}/{name}",
            "target_snapshot": {"kind": kind, "name": name, "uid": uid_a},
            "queue_semantics": "single_exactly_once_attempt",
            "execution_precondition": {"predicate": "p_target_resolves"},
            "authorization": {"predicate": "p_authorized", "must_remain_valid": True}
        },
        "attacker_capability_tier": cap_tier,
        "initial_state": {
            "timestamp_s": 0,
            "facts": {
                "original_compromised": True,
                f"{sid_key}_current_uid": uid_a,
                "irrelevant_annotation": "v1",
                "action_authorized": True
            },
            "resources": [
                {"kind": kind, "name": name, "uid": uid_a, "active": True, "security_state": "compromised"},
                {"kind": kind, "name": name, "uid": uid_b, "active": False, "security_state": "clean"}
            ],
            "processes": [], "network_bindings": [], "sessions": [], "context": {}, "evidence": [], "queue": []
        },
        "predicates": {
            "p_original_compromised": {"path": "/facts/original_compromised", "op": "eq", "value": True,
                                        "evidence_class": "H", "description": "Historical compromise applies to the original object."},
            "p_same_uid": {"path": f"/facts/{sid_key}_current_uid", "op": "eq", "value": uid_a,
                           "evidence_class": "B", "description": "Reusable logical reference still resolves to the original UID."},
            "p_target_resolves": {"path": f"/facts/{sid_key}_current_uid", "op": "ne", "value": None,
                                   "evidence_class": "B", "description": "Target reference resolves to a concrete object."},
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True,
                              "evidence_class": "DERIVED", "description": "Response executor remains technically authorized."}
        },
        "policy_profiles": {
            "balanced": {"justification": {"predicate": "p_original_compromised"},
                         "binding": {"predicate": "p_same_uid"}, "scope": True}
        },
        "transitions": [
            {"transition_id": "irrelevant_change", "title": "Irrelevant metadata change", "actor": "environment",
             "capability_tier": "NONE", "duration_s": 0.1, "cost_ops": 0, "preconditions": True,
             "effects": [{"op": "set", "path": "/facts/irrelevant_annotation", "value": "v2"}],
             "notes": "", "adversarially_induced": False, "causal_parent_ids": []},
            {"transition_id": "remove_original", "title": "Attacker removes original compromised object",
             "actor": "attacker", "capability_tier": cap_tier, "duration_s": 0.8, "cost_ops": 1, "preconditions": True,
             "effects": [{"op": "set", "path": f"/facts/{sid_key}_current_uid", "value": None},
                         {"op": "set", "path": "/resources/0/active", "value": False}],
             "notes": "", "adversarially_induced": True, "causal_parent_ids": []},
            {"transition_id": "create_successor", "title": "Controller/platform creates clean successor under reusable reference",
             "actor": "environment", "capability_tier": "NONE", "duration_s": 0.8, "cost_ops": 0, "preconditions": True,
             "effects": [{"op": "set", "path": f"/facts/{sid_key}_current_uid", "value": uid_b},
                         {"op": "set", "path": "/resources/1/active", "value": True}],
             "notes": "", "adversarially_induced": True, "causal_parent_ids": ["remove_original"]}
        ],
        "worlds": {
            "W0": {"commit_deadline_s": deadline_s, "transition_path": [], "control_semantics": "stable"},
            "W1": {"commit_deadline_s": deadline_s, "transition_path": ["irrelevant_change"], "control_semantics": "irrelevant_change"},
            "W2": {"commit_deadline_s": deadline_s, "transition_path": ["remove_original", "create_successor"], "control_semantics": "ea_toctou"},
            "W3": {"commit_deadline_s": 0.4, "transition_path": ["remove_original", "create_successor"], "control_semantics": "deadline_miss"}
        },
        "provenance": {
            "platform_basis": f"{kind} logical references may be reused while immutable object identity changes.",
            "incident_basis": "A queued response is selected against a confirmed compromised object.",
            "policy_basis": "Historical compromise remains valid only for the original resource instance.",
            "limitations": "Exact replacement timing is environment-specific and is parameterized."
        },
        "severity": severity,
        "notes": f"v9 extended confirmatory pilot template ({kind}); same causal structure as locked F1.2/F1.3, new resource kind/capability/deadline/severity combination.",
        "primary_policy": "balanced",
        "justification_bases_by_policy": {"balanced": [["p_original_compromised"]]},
        "justification_bases": [["p_original_compromised"]],
        "parameterizations": [
            {"id": "P1", "description": "Minimal topology with only essential resources and evidence."},
            {"id": "P2", "description": "Distractor topology with benign resources and irrelevant evidence."},
            {"id": "P3", "description": "Noisy SOC context with non-material alerts and unrelated transitions."},
            {"id": "P4", "description": "High-impact variant with larger benign background or higher criticality."}
        ],
        "native_controls": [
            {"control_id": "native_uid_precondition", "control_type": "uid_precondition", "predicate_ids": ["p_same_uid"],
             "normalized_cost_units": 0.0, "applicable": True, "notes": "Atomic immutable-identity precondition; live behavior is validated separately."}
        ],
        "refresh_model": {
            "cost_unit": "normalized_query_unit",
            "query_costs": {"immutable:p_original_compromised": 0.0, f"query:/facts/{sid_key}_current_uid": 1.0},
            "predicate_query_ids": {"p_original_compromised": "immutable:p_original_compromised",
                                     "p_same_uid": f"query:/facts/{sid_key}_current_uid",
                                     "p_target_resolves": f"query:/facts/{sid_key}_current_uid"},
            "query_observed_at_s": {"immutable:p_original_compromised": 0.0, f"query:/facts/{sid_key}_current_uid": 0.0}
        }
    }

    # Compute (not guess) expected_commit / expected_commit_by_policy / expected_attack_path_completed via the oracle.
    for wn, w in scenario["worlds"].items():
        out, full = compute_expected_commit_by_policy(scenario, w)
        w["expected_commit_by_policy"] = out
        w["expected_commit"] = out["balanced"]
        w["expected_attack_path_completed"] = full
    return scenario


def main():
    for spec in SPECS:
        s = build_template(*spec)
        path = OUT / f"F1_{spec[0]}.json"
        path.write_text(json.dumps(s, indent=2))
        print(f"wrote {path.name}: W2 balanced={s['worlds']['W2']['expected_commit_by_policy']['balanced']}  W3 balanced={s['worlds']['W3']['expected_commit_by_policy']['balanced']}")


if __name__ == "__main__":
    main()
