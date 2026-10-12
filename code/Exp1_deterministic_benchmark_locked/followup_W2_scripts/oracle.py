"""
Self-contained oracle logic, copied verbatim in behavior from the locked package's
deterministic/validate_all_scenarios.py (world/ep/ee/bases functions) so that new
scenario templates for the v9 extended confirmatory study can have their
expected_commit_by_policy fields computed (not guessed) using the exact same
semantics as the locked engine. This file does not modify or import anything from
inside the locked flashir_complete_v8_1_1 package -- it is an independent,
behavior-equivalent re-implementation used only to author new, honestly-labeled
extension scenarios.
"""
import copy


def get(o, p):
    cur = o
    for t in p.strip("/").split("/"):
        if not t:
            continue
        cur = cur[int(t)] if isinstance(cur, list) else cur[t]
    return cur


def setv(o, p, v):
    parts = p.strip("/").split("/")
    cur = o
    for t in parts[:-1]:
        cur = cur[int(t)] if isinstance(cur, list) else cur[t]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = v
    else:
        cur[last] = v


def ep(pid, s, st):
    p = s["predicates"][pid]
    a = get(st, p["path"])
    b = p["value"]
    if p["op"] == "eq":
        return a == b
    if p["op"] == "ne":
        return a != b
    if p["op"] == "contains":
        return b in a
    if p["op"] == "subseteq":
        return set(a).issubset(set(b))
    raise ValueError(p["op"])


def ee(e, s, st):
    if isinstance(e, bool):
        return e
    if "predicate" in e:
        return ep(e["predicate"], s, st)
    if "all" in e:
        return all(ee(x, s, st) for x in e["all"])
    if "any" in e:
        return any(ee(x, s, st) for x in e["any"])
    if "not" in e:
        return not ee(e["not"], s, st)
    raise ValueError(e)


def world(s, w):
    st = copy.deepcopy(s["initial_state"])
    tm = {t["transition_id"]: t for t in s["transitions"]}
    elapsed = 0
    done = []
    for tid in w["transition_path"]:
        t = tm[tid]
        if any(p not in done for p in t.get("causal_parent_ids", [])):
            break
        if elapsed + t["duration_s"] > w["commit_deadline_s"]:
            break
        if not ee(t.get("preconditions", True), s, st):
            break
        elapsed += t["duration_s"]
        for ef in t["effects"]:
            setv(st, ef["path"], copy.deepcopy(ef["value"]))
        done.append(tid)
    st["timestamp_s"] = w["commit_deadline_s"]
    return st, done


def compute_expected_commit_by_policy(scenario, w):
    st, done = world(scenario, w)
    out = {}
    for prof, p in scenario["policy_profiles"].items():
        auth = ep(scenario["action"]["authorization"]["predicate"], scenario, st)
        valid = auth and ee(p["justification"], scenario, st) and ee(p["binding"], scenario, st) and ee(p["scope"], scenario, st)
        out[prof] = "EXECUTE" if valid else "ABORT_REPLAN"
    full = done == w["transition_path"]
    return out, full
