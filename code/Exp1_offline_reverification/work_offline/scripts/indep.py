"""Independent re-implementation of the DCJI semantics used for brute-force checks.

Written from the scenario SPEC (JSON schema + paper definitions), not by calling
flashir_core. What is necessarily SHARED with the locked core (same spec, so any
spec-level error would be common to both):
  * JSON-pointer semantics for predicate paths and 'set' effects;
  * predicate ops eq/ne/contains/subseteq and all/any/not expressions;
  * commit validity V = auth AND justification AND binding AND scope;
    attack goal = auth AND executable AND NOT V;
  * attacker model: attacker transitions with tier <= capability, environment
    transitions only if adversarially_induced; transitions at most once; causal
    parents; preconditions; cumulative duration <= deadline;
  * hybrid view = cached snapshot (initial_state) with the paths of refreshed
    predicates copied from the commit state; material predicates exclude the
    authorization predicate (so authorization is never re-read, finding C8).
NOT shared: code, traversal order, deduplication, candidate pruning (we use the
FULL query universe), subset enumeration, cost arithmetic (exact Fractions).
"""
import copy, json, itertools
from fractions import Fraction

CAP = {"NONE": 0, "C0": 1, "C1": 2, "C2": 3, "C3": 4}


def _tok(t):
    return t.replace("~1", "/").replace("~0", "~")


def jget(o, path):
    cur = o
    for t in [x for x in path.strip("/").split("/") if x]:
        t = _tok(t)
        cur = cur[int(t)] if isinstance(cur, list) else cur[t]
    return cur


def jset(o, path, v):
    parts = path.strip("/").split("/")
    cur = o
    for t in parts[:-1]:
        t = _tok(t)
        cur = cur[int(t)] if isinstance(cur, list) else cur[t]
    last = _tok(parts[-1])
    if isinstance(cur, list):
        cur[int(last)] = v
    else:
        cur[last] = v


def pred(s, pid, st):
    p = s["predicates"][pid]
    got, want, op = jget(st, p["path"]), p["value"], p["op"]
    if op == "eq":
        return got == want
    if op == "ne":
        return got != want
    if op == "contains":
        return want in got
    if op == "subseteq":
        return set(got) <= set(want)
    raise ValueError(op)


def ex(s, e, st):
    if e is True or e is False:
        return e
    if "predicate" in e:
        return pred(s, e["predicate"], st)
    if "all" in e:
        return all(ex(s, x, st) for x in e["all"])
    if "any" in e:
        return any(ex(s, x, st) for x in e["any"])
    if "not" in e:
        return not ex(s, e["not"], st)
    raise ValueError(e)


def refs(e):
    if e is True or e is False:
        return set()
    if "predicate" in e:
        return {e["predicate"]}
    k = "all" if "all" in e else "any" if "any" in e else None
    if k:
        out = set()
        for x in e[k]:
            out |= refs(x)
        return out
    return refs(e["not"])


def material(s, prof):
    pol = s["policy_profiles"][prof]
    m = refs(pol["justification"]) | refs(pol["binding"]) | refs(pol["scope"]) | refs(s["action"]["execution_precondition"])
    m.discard(s["action"]["authorization"]["predicate"])
    return m


def comps(s, st, prof):
    pol = s["policy_profiles"][prof]
    a = pred(s, s["action"]["authorization"]["predicate"], st)
    j = ex(s, pol["justification"], st)
    b = ex(s, pol["binding"], st)
    sc = ex(s, pol["scope"], st)
    x = ex(s, s["action"]["execution_precondition"], st)
    v = a and j and b and sc
    return {"auth": a, "just": j, "bind": b, "scope": sc, "exe": x, "valid": v, "goal": a and x and not v}


def in_model(t, cap):
    if t["actor"] == "attacker":
        return CAP[t["capability_tier"]] <= CAP[cap]
    if t["actor"] == "environment":
        return bool(t.get("adversarially_induced", False))
    return False


def canon(st):
    d = {k: v for k, v in st.items() if k != "timestamp_s"}
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def reachable(s, deadline, tset, prune_unauth=True):
    """All states reachable by applying transitions of tset (each at most once) with
    cumulative duration <= deadline. Returns {canon: (state, (cost, elapsed, len), path)}.
    prune_unauth=True: do not extend paths through states whose authorization is false
    (matches the locked traversal). BFS by level; independent of locked DFS order."""
    auth = s["action"]["authorization"]["predicate"]
    start = copy.deepcopy(s["initial_state"])
    frontier = [(start, (), 0.0, 0)]
    seen = set()
    out = {}
    while frontier:
        nxt = []
        for st, done, el, c in frontier:
            ck = canon(st)
            k = (ck, frozenset(done))
            if k in seen:
                continue
            seen.add(k)
            sc = (c, el, len(done))
            if ck not in out or sc < out[ck][1]:
                out[ck] = (st, sc, list(done))
            if prune_unauth and not pred(s, auth, st):
                continue
            for t in tset:
                tid = t["transition_id"]
                if tid in done or el + t["duration_s"] > deadline:
                    continue
                if any(p not in done for p in t.get("causal_parent_ids", [])):
                    continue
                if not ex(s, t.get("preconditions", True), st):
                    continue
                ns = copy.deepcopy(st)
                for e in t["effects"]:
                    assert e["op"] == "set"
                    jset(ns, e["path"], copy.deepcopy(e["value"]))
                nxt.append((ns, done + (tid,), el + t["duration_s"], c + t["cost_ops"]))
        frontier = nxt
    return out


def model_transitions(s):
    return [t for t in s["transitions"] if in_model(t, s["attacker_capability_tier"])]


def goals_of(s, deadline, prof, prune_unauth=True, tset=None):
    tset = model_transitions(s) if tset is None else tset
    R = reachable(s, deadline, tset, prune_unauth)
    return {k: v for k, v in R.items() if comps(s, v[0], prof)["goal"]}


def qcost(v):
    if isinstance(v, int):
        return Fraction(v)
    return Fraction(str(float(v)))


def hybrid(s, cached, cur, Q, prof, mode="material"):
    qmap = s["refresh_model"]["predicate_query_ids"]
    pids = material(s, prof) if mode == "material" else set(s["predicates"])
    paths = sorted({s["predicates"][p]["path"] for p in pids if p in qmap and qmap[p] in Q})
    h = copy.deepcopy(cached)
    for path in paths:
        jset(h, path, copy.deepcopy(jget(cur, path)))
    return h


def brute_min(s, prof, goal_states, universe, mode="material", max_n=22):
    """Exhaustive over ALL subsets of `universe`. Returns dict with min exact cost, the
    (cost, |Q|, sorted Q) tie-break winner, number of feasible subsets, subsets tried."""
    U = sorted(universe)
    if len(U) > max_n:
        return {"status": "TOO_LARGE", "n": len(U)}
    costs = {q: qcost(s["refresh_model"]["query_costs"][q]) for q in U}
    init = s["initial_state"]
    best = None
    feas = 0
    tried = 0
    for r in range(len(U) + 1):
        for combo in itertools.combinations(U, r):
            tried += 1
            Q = set(combo)
            if all(not comps(s, hybrid(s, init, g, Q, prof, mode), prof)["valid"] for g in goal_states):
                feas += 1
                key = (sum((costs[q] for q in combo), Fraction(0)), len(combo), combo)
                if best is None or key < best:
                    best = key
    return {"status": "OK", "n": len(U), "tried": tried, "feasible": feas,
            "min_cost": None if best is None else best[0], "argmin": None if best is None else list(best[2])}


def falsify(s, pid, st):
    """Return a copy of st in which predicate pid is false (out-of-model mutation)."""
    p = s["predicates"][pid]
    ns = copy.deepcopy(st)
    v, op = p["value"], p["op"]
    if op == "eq":
        nv = (not v) if isinstance(v, bool) else "__MUTATED__"
    elif op == "ne":
        nv = copy.deepcopy(v)
    elif op == "contains":
        cur = jget(st, p["path"])
        nv = "" if isinstance(cur, str) else []
    elif op == "subseteq":
        nv = list(v) + ["__MUTATED__"]
    else:
        raise ValueError(op)
    jset(ns, p["path"], nv)
    return ns
