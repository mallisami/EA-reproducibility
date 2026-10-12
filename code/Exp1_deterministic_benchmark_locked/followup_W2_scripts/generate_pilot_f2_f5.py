"""
Generates v9 extended-study templates for families F2 (network-identity
rebinding), F3 (runtime-identity rebinding), F4 (scope drift) and F5
(multi-basis justification), each following the exact causal/logical structure
of the corresponding locked heldout example (F2.2, F3.2, F4.2, F5.2), varied
across genuinely different resource/target labels, attacker capability tiers,
deadlines and severities. All expected_commit_by_policy / path-completion
fields are COMPUTED via oracle.py, not guessed.
"""
import json
from pathlib import Path
from oracle import compute_expected_commit_by_policy

OUT = Path(__file__).resolve().parents[1] / "scenarios" / "pilot"
OUT.mkdir(parents=True, exist_ok=True)

CAPS = ["C1", "C2", "C3"]
SEVS = ["S1", "S2", "S3", "S4"]


def finalize(scenario):
    for wn, w in scenario["worlds"].items():
        out, full = compute_expected_commit_by_policy(scenario, w)
        w["expected_commit_by_policy"] = out
        w["expected_commit"] = out["balanced"]
        w["expected_attack_path_completed"] = full
    return scenario


# ---------------- F2: network-identity rebinding (single dynamic predicate) ----------------
F2_SPECS = [
    (7, "10.0.4.22", "VM_C2_7A", "VM_C2_7B", "C1", 5, "S1"),
    (8, "10.0.4.23", "VM_C2_8A", "VM_C2_8B", "C2", 5, "S2"),
    (9, "10.0.4.24", "VM_C2_9A", "VM_C2_9B", "C3", 5, "S3"),
    (10, "10.0.4.25", "VM_C2_10A", "VM_C2_10B", "C1", 8, "S2"),
    (11, "10.0.4.26", "VM_C2_11A", "VM_C2_11B", "C2", 3, "S1"),
    (12, "10.0.4.27", "VM_C2_12A", "VM_C2_12B", "C3", 10, "S4"),
    (13, "10.0.4.28", "VM_C2_13A", "VM_C2_13B", "C1", 5, "S2"),
    (14, "10.0.4.29", "VM_C2_14A", "VM_C2_14B", "C2", 5, "S3"),
    (15, "10.0.4.30", "VM_C2_15A", "VM_C2_15B", "C3", 6, "S1"),
    (16, "10.0.4.31", "VM_C2_16A", "VM_C2_16B", "C1", 7, "S4"),
    (17, "10.0.4.32", "VM_C2_17A", "VM_C2_17B", "C2", 9, "S2"),
    (18, "10.0.4.33", "VM_C2_18A", "VM_C2_18B", "C3", 4, "S3"),
    (19, "10.0.4.34", "VM_C2_19A", "VM_C2_19B", "C1", 5, "S1"),
    (20, "10.0.4.35", "VM_C2_20A", "VM_C2_20B", "C2", 5, "S2"),
    (21, "10.0.4.36", "VM_C2_21A", "VM_C2_21B", "C3", 5, "S3"),
]


def build_f2(num, ip, uid_a, uid_b, cap, deadline_s, sev):
    key = f"F2_{num}_binding"
    return {
        "scenario_id": f"F2.{num}", "title": f"Network Identity Reallocation ({ip})", "family": "F2",
        "realism_tier": "R2/R3", "scenario_role": "positive_ea_toctou", "development": False,
        "action": {"action_type": "block_network_identity", "target_reference": f"public-ip/{ip}",
                   "target_snapshot": {"identifier": f"public-ip/{ip}", "resource_uid": uid_a},
                   "queue_semantics": "single_exactly_once_attempt", "execution_precondition": {"predicate": "p_target_resolves"},
                   "authorization": {"predicate": "p_authorized", "must_remain_valid": True}},
        "attacker_capability_tier": cap,
        "initial_state": {"timestamp_s": 0, "facts": {"original_malicious": True, key: uid_a, "irrelevant_metric": 0, "action_authorized": True},
                           "resources": [], "processes": [], "network_bindings": [{"identifier": f"public-ip/{ip}", "resource_uid": uid_a}],
                           "sessions": [], "context": {}, "evidence": [], "queue": []},
        "predicates": {
            "p_original_malicious": {"path": "/facts/original_malicious", "op": "eq", "value": True, "evidence_class": "H", "description": "Original network-bound resource was malicious."},
            "p_same_binding": {"path": f"/facts/{key}", "op": "eq", "value": uid_a, "evidence_class": "B", "description": "Network identifier still denotes the original malicious resource."},
            "p_target_resolves": {"path": f"/facts/{key}", "op": "ne", "value": None, "evidence_class": "B", "description": "Network identifier resolves to a concrete target."},
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True, "evidence_class": "DERIVED", "description": "Response executor remains technically authorized."}
        },
        "policy_profiles": {"balanced": {"justification": {"predicate": "p_original_malicious"}, "binding": {"predicate": "p_same_binding"}, "scope": True}},
        "transitions": [
            {"transition_id": "irrelevant_change", "title": "Irrelevant telemetry change", "actor": "environment", "capability_tier": "NONE",
             "duration_s": 0.1, "cost_ops": 0, "preconditions": True, "effects": [{"op": "set", "path": "/facts/irrelevant_metric", "value": 1}],
             "notes": "", "adversarially_induced": False, "causal_parent_ids": []},
            {"transition_id": "release_original", "title": "Attacker releases original binding", "actor": "attacker", "capability_tier": cap,
             "duration_s": 0.9, "cost_ops": 1, "preconditions": True, "effects": [{"op": "set", "path": f"/facts/{key}", "value": None}],
             "notes": "", "adversarially_induced": True, "causal_parent_ids": []},
            {"transition_id": "assign_successor", "title": "Platform/allocator maps identifier to benign successor", "actor": "environment",
             "capability_tier": "NONE", "duration_s": 1.1, "cost_ops": 0, "preconditions": True,
             "effects": [{"op": "set", "path": f"/facts/{key}", "value": uid_b}], "notes": "", "adversarially_induced": True, "causal_parent_ids": ["release_original"]}
        ],
        "worlds": {
            "W0": {"commit_deadline_s": deadline_s, "transition_path": [], "control_semantics": "stable"},
            "W1": {"commit_deadline_s": deadline_s, "transition_path": ["irrelevant_change"], "control_semantics": "irrelevant_change"},
            "W2": {"commit_deadline_s": deadline_s, "transition_path": ["release_original", "assign_successor"], "control_semantics": "ea_toctou"},
            "W3": {"commit_deadline_s": 0.4, "transition_path": ["release_original", "assign_successor"], "control_semantics": "deadline_miss"}
        },
        "provenance": {"platform_basis": "Dynamic network/service identity is explicitly modeled and is not assumed to be immutable.",
                       "incident_basis": "SOC queues a network-targeted containment action from a malicious binding.",
                       "policy_basis": "The action remains valid only while the identifier denotes the original malicious resource.",
                       "limitations": "Specific reuse/reassignment mechanics require environment-specific validation."},
        "severity": sev, "notes": "v9 extended confirmatory pilot template (F2); same causal structure as locked F2.2.",
        "primary_policy": "balanced", "justification_bases_by_policy": {"balanced": [["p_original_malicious"]]},
        "justification_bases": [["p_original_malicious"]],
        "parameterizations": [
            {"id": "P1", "description": "Minimal topology with only essential resources and evidence."},
            {"id": "P2", "description": "Distractor topology with benign resources and irrelevant evidence."},
            {"id": "P3", "description": "Noisy SOC context with non-material alerts and unrelated transitions."},
            {"id": "P4", "description": "High-impact variant with larger benign background or higher criticality."}
        ],
        "native_controls": [],
        "refresh_model": {"cost_unit": "normalized_query_unit", "query_costs": {"immutable:p_original_malicious": 0.0, f"query:/facts/{key}": 1.0},
                           "predicate_query_ids": {"p_original_malicious": "immutable:p_original_malicious", "p_same_binding": f"query:/facts/{key}", "p_target_resolves": f"query:/facts/{key}"},
                           "query_observed_at_s": {"immutable:p_original_malicious": 0.0, f"query:/facts/{key}": 0.0}}
    }


# ---------------- F3: runtime-identity rebinding (two dynamic predicates) ----------------
F3_SPECS = [
    (7, "worker-7", "C1", 3, "S1"), (8, "worker-8", "C2", 3, "S2"), (9, "worker-9", "C3", 3, "S3"),
    (10, "worker-10", "C1", 5, "S2"), (11, "worker-11", "C2", 2, "S1"), (12, "worker-12", "C3", 6, "S4"),
    (13, "worker-13", "C1", 3, "S2"), (14, "worker-14", "C2", 3, "S3"), (15, "worker-15", "C3", 4, "S1"),
    (16, "worker-16", "C1", 7, "S4"), (17, "worker-17", "C2", 3, "S2"), (18, "worker-18", "C3", 3, "S3"),
    (19, "worker-19", "C1", 5, "S1"), (20, "worker-20", "C2", 5, "S2"), (21, "worker-21", "C3", 5, "S3"),
]


def build_f3(num, name, cap, deadline_s, sev):
    key = f"F3_{num}_instance"
    return {
        "scenario_id": f"F3.{num}", "title": f"Container Restart and PID 1 Replacement ({name})", "family": "F3",
        "realism_tier": "R1/R2", "scenario_role": "positive_ea_toctou", "development": False,
        "action": {"action_type": "kill_process", "target_reference": f"container/{name}:pid1", "target_snapshot": {"instance": f"{num}A", "reference": f"container/{name}:pid1"},
                   "queue_semantics": "single_exactly_once_attempt", "execution_precondition": {"predicate": "p_target_resolves"},
                   "authorization": {"predicate": "p_authorized", "must_remain_valid": True}},
        "attacker_capability_tier": cap,
        "initial_state": {"timestamp_s": 0, "facts": {"original_malicious": True, "original_active": True, key: f"{num}A", "irrelevant_cpu": 10, "action_authorized": True},
                           "resources": [], "processes": [{"process_id": f"{num}A", "active": True, "security_state": "malicious"}, {"process_id": f"{num}B", "active": False, "security_state": "benign"}],
                           "network_bindings": [], "sessions": [], "context": {}, "evidence": [], "queue": []},
        "predicates": {
            "p_original_malicious": {"path": "/facts/original_malicious", "op": "eq", "value": True, "evidence_class": "H", "description": "Original runtime instance is confirmed malicious."},
            "p_original_active": {"path": "/facts/original_active", "op": "eq", "value": True, "evidence_class": "L", "description": "Original malicious runtime instance remains active."},
            "p_same_instance": {"path": f"/facts/{key}", "op": "eq", "value": f"{num}A", "evidence_class": "B", "description": "Reusable runtime reference still denotes the original instance."},
            "p_target_resolves": {"path": f"/facts/{key}", "op": "ne", "value": None, "evidence_class": "B", "description": "Runtime reference resolves to an actuation target."},
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True, "evidence_class": "DERIVED", "description": "Response executor remains technically authorized."}
        },
        "policy_profiles": {"balanced": {"justification": {"all": [{"predicate": "p_original_malicious"}, {"predicate": "p_original_active"}]}, "binding": {"predicate": "p_same_instance"}, "scope": True}},
        "transitions": [
            {"transition_id": "irrelevant_change", "title": "Irrelevant CPU change", "actor": "environment", "capability_tier": "NONE", "duration_s": 0.1, "cost_ops": 0,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/irrelevant_cpu", "value": 20}], "notes": "", "adversarially_induced": False, "causal_parent_ids": []},
            {"transition_id": "stop_original", "title": "Attacker stops original malicious runtime instance", "actor": "attacker", "capability_tier": cap, "duration_s": 0.5, "cost_ops": 1,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/original_active", "value": False}, {"op": "set", "path": f"/facts/{key}", "value": None}, {"op": "set", "path": "/processes/0/active", "value": False}],
             "notes": "", "adversarially_induced": True, "causal_parent_ids": []},
            {"transition_id": "reuse_reference", "title": "Benign replacement receives reusable runtime reference", "actor": "environment", "capability_tier": "NONE", "duration_s": 0.5, "cost_ops": 0,
             "preconditions": True, "effects": [{"op": "set", "path": f"/facts/{key}", "value": f"{num}B"}, {"op": "set", "path": "/processes/1/active", "value": True}],
             "notes": "", "adversarially_induced": True, "causal_parent_ids": ["stop_original"]}
        ],
        "worlds": {
            "W0": {"commit_deadline_s": deadline_s, "transition_path": [], "control_semantics": "stable"},
            "W1": {"commit_deadline_s": deadline_s, "transition_path": ["irrelevant_change"], "control_semantics": "irrelevant_change"},
            "W2": {"commit_deadline_s": deadline_s, "transition_path": ["stop_original", "reuse_reference"], "control_semantics": "ea_toctou"},
            "W3": {"commit_deadline_s": 0.25, "transition_path": ["stop_original", "reuse_reference"], "control_semantics": "deadline_miss"}
        },
        "provenance": {"platform_basis": "Runtime identifiers and logical service references can be reused across process/container instances.",
                       "incident_basis": "EDR queues termination of a specific malicious runtime instance.", "policy_basis": "Kill remains valid only for the same active malicious instance.",
                       "limitations": "Exact reuse timing is runtime/OS specific."},
        "severity": sev, "notes": "v9 extended confirmatory pilot template (F3); same causal structure as locked F3.2.",
        "primary_policy": "balanced", "justification_bases_by_policy": {"balanced": [["p_original_active", "p_original_malicious"]]},
        "justification_bases": [["p_original_active", "p_original_malicious"]],
        "parameterizations": [
            {"id": "P1", "description": "Minimal topology with only essential resources and evidence."},
            {"id": "P2", "description": "Distractor topology with benign resources and irrelevant evidence."},
            {"id": "P3", "description": "Noisy SOC context with non-material alerts and unrelated transitions."},
            {"id": "P4", "description": "High-impact variant with larger benign background or higher criticality."}
        ],
        "native_controls": [],
        "refresh_model": {"cost_unit": "normalized_query_unit",
                           "query_costs": {"query:/facts/original_active": 1.0, "immutable:p_original_malicious": 0.0, f"query:/facts/{key}": 1.0},
                           "predicate_query_ids": {"p_original_active": "query:/facts/original_active", "p_original_malicious": "immutable:p_original_malicious", "p_same_instance": f"query:/facts/{key}", "p_target_resolves": f"query:/facts/{key}"},
                           "query_observed_at_s": {"query:/facts/original_active": 0.0, "immutable:p_original_malicious": 0.0, f"query:/facts/{key}": 0.0}}
    }


# ---------------- F4: scope drift (single dynamic predicate) ----------------
F4_SPECS = [
    (7, "zone-a", "C1", 3, "S1"), (8, "zone-b", "C2", 3, "S2"), (9, "zone-c", "C3", 3, "S3"),
    (10, "zone-d", "C1", 5, "S2"), (11, "zone-e", "C2", 2, "S1"), (12, "zone-f", "C3", 6, "S4"),
    (13, "zone-g", "C1", 3, "S2"), (14, "zone-h", "C2", 3, "S3"), (15, "zone-i", "C3", 4, "S1"),
    (16, "zone-j", "C1", 7, "S4"), (17, "zone-k", "C2", 3, "S2"), (18, "zone-l", "C3", 3, "S3"),
    (19, "zone-m", "C1", 5, "S1"), (20, "zone-n", "C2", 5, "S2"), (21, "zone-o", "C3", 5, "S3"),
]


def build_f4(num, zone, cap, deadline_s, sev):
    return {
        "scenario_id": f"F4.{num}", "title": f"NamespaceSelector Expansion ({zone})", "family": "F4",
        "realism_tier": "R1", "scenario_role": "positive_ea_toctou", "development": False,
        "action": {"action_type": "restrict_scope", "target_reference": f"namespaceSelector/{zone}=true", "target_snapshot": {"members": [f"N{num}"]},
                   "queue_semantics": "single_exactly_once_attempt", "execution_precondition": True, "authorization": {"predicate": "p_authorized", "must_remain_valid": True}},
        "attacker_capability_tier": cap,
        "initial_state": {"timestamp_s": 0, "facts": {"threat_relevant": True, "scope_members": [f"N{num}"], "irrelevant_metric": 0, "action_authorized": True},
                           "resources": [], "processes": [], "network_bindings": [], "sessions": [], "context": {}, "evidence": [], "queue": []},
        "predicates": {
            "p_threat": {"path": "/facts/threat_relevant", "op": "eq", "value": True, "evidence_class": "H", "description": "Original selected scope contains a confirmed threat."},
            "p_scope_safe": {"path": "/facts/scope_members", "op": "eq", "value": [f"N{num}"], "evidence_class": "DERIVED", "description": "Affected set remains the originally intended threat-relevant scope."},
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True, "evidence_class": "DERIVED", "description": "Response executor remains technically authorized."}
        },
        "policy_profiles": {"balanced": {"justification": {"predicate": "p_threat"}, "binding": True, "scope": {"predicate": "p_scope_safe"}}},
        "transitions": [
            {"transition_id": "irrelevant_change", "title": "Irrelevant monitoring update", "actor": "environment", "capability_tier": "NONE", "duration_s": 0.1, "cost_ops": 0,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/irrelevant_metric", "value": 1}], "notes": "", "adversarially_induced": False, "causal_parent_ids": []},
            {"transition_id": "expand_scope", "title": "Attacker induces scope membership expansion", "actor": "attacker", "capability_tier": cap, "duration_s": 0.8, "cost_ops": 1,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/scope_members", "value": [f"N{num}", f"N{num}X"]}], "notes": "", "adversarially_induced": True, "causal_parent_ids": []}
        ],
        "worlds": {
            "W0": {"commit_deadline_s": deadline_s, "transition_path": [], "control_semantics": "stable"},
            "W1": {"commit_deadline_s": deadline_s, "transition_path": ["irrelevant_change"], "control_semantics": "irrelevant_change"},
            "W2": {"commit_deadline_s": deadline_s, "transition_path": ["expand_scope"], "control_semantics": "ea_toctou"},
            "W3": {"commit_deadline_s": 0.4, "transition_path": ["expand_scope"], "control_semantics": "deadline_miss"}
        },
        "provenance": {"platform_basis": "Selector/tag/backend membership is evaluated against the set present at execution time.",
                       "incident_basis": "A response is queued when the affected set contains only threat-relevant resources.",
                       "policy_basis": "The response is valid only while its affected set remains within the intended containment scope.",
                       "limitations": "Required label/tag/membership mutation capability is modeled explicitly."},
        "severity": sev, "notes": "v9 extended confirmatory pilot template (F4); same causal structure as locked F4.2.",
        "primary_policy": "balanced", "justification_bases_by_policy": {"balanced": [["p_threat"]]}, "justification_bases": [["p_threat"]],
        "parameterizations": [
            {"id": "P1", "description": "Minimal topology with only essential resources and evidence."},
            {"id": "P2", "description": "Distractor topology with benign resources and irrelevant evidence."},
            {"id": "P3", "description": "Noisy SOC context with non-material alerts and unrelated transitions."},
            {"id": "P4", "description": "High-impact variant with larger benign background or higher criticality."}
        ],
        "native_controls": [],
        "refresh_model": {"cost_unit": "normalized_query_unit", "query_costs": {"query:/facts/scope_members": 1.0, "immutable:p_threat": 0.0},
                           "predicate_query_ids": {"p_scope_safe": "query:/facts/scope_members", "p_threat": "immutable:p_threat"},
                           "query_observed_at_s": {"query:/facts/scope_members": 0.0, "immutable:p_threat": 0.0}}
    }


# ---------------- F5: multi-basis justification (six dynamic predicates, 3 AND-pairs OR'd) ----------------
F5_SPECS = [
    (7, "C1", 5, "S1"), (8, "C2", 5, "S2"), (9, "C3", 5, "S3"), (10, "C1", 8, "S2"), (11, "C2", 3, "S1"),
    (12, "C3", 10, "S4"), (13, "C1", 5, "S2"), (14, "C2", 5, "S3"), (15, "C3", 6, "S1"), (16, "C1", 7, "S4"),
    (17, "C2", 5, "S2"), (18, "C3", 5, "S3"), (19, "C1", 5, "S1"), (20, "C2", 5, "S2"), (21, "C3", 5, "S3"),
]


def build_f5(num, cap, deadline_s, sev):
    names = ["a", "b", "c", "d", "e", "f"]
    facts = {n: True for n in names}
    facts.update({"irrelevant_alert": False, "action_authorized": True})
    return {
        "scenario_id": f"F5.{num}", "title": f"Three Independent Justification Bases (variant {num})", "family": "F5",
        "realism_tier": "R3", "scenario_role": "positive_ea_toctou", "development": False,
        "action": {"action_type": "quarantine_workload", "target_reference": f"workload/U{num}", "target_snapshot": {"uid": f"U{num}"},
                   "queue_semantics": "single_exactly_once_attempt", "execution_precondition": True, "authorization": {"predicate": "p_authorized", "must_remain_valid": True}},
        "attacker_capability_tier": cap,
        "initial_state": {"timestamp_s": 0, "facts": facts, "resources": [], "processes": [], "network_bindings": [], "sessions": [], "context": {}, "evidence": [], "queue": []},
        "predicates": {
            **{n: {"path": f"/facts/{n}", "op": "eq", "value": True, "evidence_class": "L", "description": f"Current predicate {n}."} for n in names},
            "p_authorized": {"path": "/facts/action_authorized", "op": "eq", "value": True, "evidence_class": "DERIVED", "description": "Response executor remains technically authorized."}
        },
        "policy_profiles": {"balanced": {"justification": {"any": [
            {"all": [{"predicate": "a"}, {"predicate": "b"}]},
            {"all": [{"predicate": "c"}, {"predicate": "d"}]},
            {"all": [{"predicate": "e"}, {"predicate": "f"}]}
        ]}, "binding": True, "scope": True}},
        "transitions": [
            {"transition_id": "irrelevant_change", "title": "Irrelevant alert", "actor": "environment", "capability_tier": "NONE", "duration_s": 0.1, "cost_ops": 0,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/irrelevant_alert", "value": True}], "notes": "", "adversarially_induced": False, "causal_parent_ids": []},
            {"transition_id": "cut_b1", "title": "Cut basis 1", "actor": "attacker", "capability_tier": cap, "duration_s": 0.6, "cost_ops": 1,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/a", "value": False}], "notes": "", "adversarially_induced": True, "causal_parent_ids": []},
            {"transition_id": "cut_b2", "title": "Cut basis 2", "actor": "attacker", "capability_tier": cap, "duration_s": 0.6, "cost_ops": 1,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/c", "value": False}], "notes": "", "adversarially_induced": True, "causal_parent_ids": []},
            {"transition_id": "cut_b3", "title": "Cut basis 3", "actor": "attacker", "capability_tier": cap, "duration_s": 0.6, "cost_ops": 1,
             "preconditions": True, "effects": [{"op": "set", "path": "/facts/e", "value": False}], "notes": "", "adversarially_induced": True, "causal_parent_ids": []}
        ],
        "worlds": {
            "W0": {"commit_deadline_s": deadline_s, "transition_path": [], "control_semantics": "stable"},
            "W1": {"commit_deadline_s": deadline_s, "transition_path": ["irrelevant_change"], "control_semantics": "irrelevant_change"},
            "W2": {"commit_deadline_s": deadline_s, "transition_path": ["cut_b1", "cut_b2", "cut_b3"], "control_semantics": "ea_toctou"},
            "W3": {"commit_deadline_s": 0.4, "transition_path": ["cut_b1", "cut_b2", "cut_b3"], "control_semantics": "deadline_miss"}
        },
        "provenance": {"platform_basis": "Controlled synthetic policy structure used to isolate justification semantics.",
                       "incident_basis": "Multiple independent active or historical evidence bases support a pending response.",
                       "policy_basis": "Commit validity follows the explicit Boolean response policy.",
                       "limitations": "The exact Boolean structure is methodological and is not claimed as a universal SOC policy."},
        "severity": sev, "notes": "v9 extended confirmatory pilot template (F5); same causal structure as locked F5.2.",
        "primary_policy": "balanced", "justification_bases_by_policy": {"balanced": [["a", "b"], ["c", "d"], ["e", "f"]]},
        "justification_bases": [["a", "b"], ["c", "d"], ["e", "f"]],
        "parameterizations": [
            {"id": "P1", "description": "Minimal topology with only essential resources and evidence."},
            {"id": "P2", "description": "Distractor topology with benign resources and irrelevant evidence."},
            {"id": "P3", "description": "Noisy SOC context with non-material alerts and unrelated transitions."},
            {"id": "P4", "description": "High-impact variant with larger benign background or higher criticality."}
        ],
        "native_controls": [],
        "refresh_model": {"cost_unit": "normalized_query_unit", "query_costs": {f"query:/facts/{n}": 1.0 for n in names},
                           "predicate_query_ids": {n: f"query:/facts/{n}" for n in names},
                           "query_observed_at_s": {f"query:/facts/{n}": 0.0 for n in names}}
    }


def main():
    count = 0
    for spec in F2_SPECS:
        s = finalize(build_f2(*spec))
        (OUT / f"F2_{spec[0]}.json").write_text(json.dumps(s, indent=2)); count += 1
    for spec in F3_SPECS:
        s = finalize(build_f3(*spec))
        (OUT / f"F3_{spec[0]}.json").write_text(json.dumps(s, indent=2)); count += 1
    for spec in F4_SPECS:
        s = finalize(build_f4(*spec))
        (OUT / f"F4_{spec[0]}.json").write_text(json.dumps(s, indent=2)); count += 1
    for spec in F5_SPECS:
        s = finalize(build_f5(*spec))
        (OUT / f"F5_{spec[0]}.json").write_text(json.dumps(s, indent=2)); count += 1
    print(f"wrote {count} new templates across F2-F5")


if __name__ == "__main__":
    main()
