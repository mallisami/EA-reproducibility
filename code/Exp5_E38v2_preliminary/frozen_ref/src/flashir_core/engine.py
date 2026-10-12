
import copy
from .expr import eval_expr, pointer_set

CAP_RANK = {"NONE": 0, "C0": 1, "C1": 2, "C2": 3, "C3": 4}

def transition_map(scenario):
    return {t["transition_id"]: t for t in scenario["transitions"]}

def transition_allowed_for_attacker_model(t, attacker_capability):
    if t["actor"] == "attacker":
        return CAP_RANK[t["capability_tier"]] <= CAP_RANK[attacker_capability]
    if t["actor"] == "environment":
        return bool(t.get("adversarially_induced", False))
    return False

def can_apply_transition(t, scenario, state, completed_ids):
    if any(parent not in completed_ids for parent in t.get("causal_parent_ids", [])):
        return False
    return eval_expr(t.get("preconditions", True), scenario, state)

def apply_transition(t, scenario, state, completed_ids):
    if not can_apply_transition(t, scenario, state, completed_ids):
        raise ValueError(f"Transition {t['transition_id']} is not currently applicable")
    nxt = copy.deepcopy(state)
    for e in t["effects"]:
        if e["op"] != "set":
            raise ValueError(f"Unsupported effect operation {e['op']}")
        pointer_set(nxt, e["path"], copy.deepcopy(e["value"]))
    return nxt

def replay_path(scenario, transition_ids, deadline_s):
    state = copy.deepcopy(scenario["initial_state"])
    tmap = transition_map(scenario)
    elapsed = 0.0
    cost = 0
    completed = []
    for tid in transition_ids:
        t = tmap[tid]
        if elapsed + t["duration_s"] > deadline_s:
            break
        if not can_apply_transition(t, scenario, state, completed):
            break
        state = apply_transition(t, scenario, state, completed)
        elapsed += t["duration_s"]
        cost += t["cost_ops"]
        completed.append(tid)
    state["timestamp_s"] = deadline_s
    return {
        "state": state,
        "elapsed_s": elapsed,
        "cost_ops": cost,
        "completed_ids": completed,
        "path_completed": completed == transition_ids,
    }
