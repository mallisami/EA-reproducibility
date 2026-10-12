"""Offline-reanalysis tests for the C1/C2/C7 patches and the observability validator.

Runs against whichever flashir_core is on sys.path (pytest.ini: pythonpath = .).
* Against the PATCHED core every test must pass.
* Against the LOCKED core, test_i_fail_open_zero_cost_path asserts the documented
  fail-open behaviour (EXECUTE of an attack-goal state) and the patch-only tests skip.
"""
import copy, itertools, json, random, time, tracemalloc, unittest, warnings
from fractions import Fraction

from flashir_core import dcji, defenses
from flashir_core.expr import commit_components
from flashir_core.refresh import refresh_queries

PATCHED = hasattr(dcji, "iter_subsets_in_certificate_order")


def base_scenario(facts, justification, transitions, costs, pq, binding=True):
    s = {
        "scenario_id": "OFFLINE_TEST", "attacker_capability_tier": "C1", "primary_policy": "balanced",
        "initial_state": {"timestamp_s": 0, "facts": dict(facts, auth=True), "resources": [], "processes": [],
                          "network_bindings": [], "sessions": [], "context": {}, "evidence": [], "queue": []},
        "predicates": {n: {"path": f"/facts/{n}", "op": "eq", "value": facts[n]} for n in facts},
        "action": {"authorization": {"predicate": "p_auth"}, "execution_precondition": True},
        "policy_profiles": {"balanced": {"justification": justification, "binding": binding, "scope": True}},
        "transitions": transitions,
        "refresh_model": {"cost_unit": "normalized_query_unit", "query_costs": costs, "predicate_query_ids": pq,
                          "query_observed_at_s": {q: 0.0 for q in costs}},
    }
    s["predicates"]["p_auth"] = {"path": "/facts/auth", "op": "eq", "value": True}
    return s


def cut(tid, var, value=False, dur=1.0):
    return {"transition_id": tid, "actor": "attacker", "capability_tier": "C1", "duration_s": dur, "cost_ops": 1,
            "preconditions": True, "effects": [{"op": "set", "path": f"/facts/{var}", "value": value}],
            "adversarially_induced": True, "causal_parent_ids": []}


def fail_open_scenario():
    # Justification = x AND y. x is only observable through a zero-cost ("immutable") query,
    # yet an in-model attacker transition flips x. No refresh can reveal the change.
    return base_scenario({"x": True, "y": True}, {"all": [{"predicate": "x"}, {"predicate": "y"}]},
                         [cut("flip_x", "x")], {"imm:x": 0.0, "q:y": 1.0}, {"x": "imm:x", "y": "q:y"})


def wide_or_scenario(n):
    # E12c-style: justification = OR of n independent bases, one step cuts all of them.
    # The only certificate is the full set, so the locked planner must visit all 2^n subsets.
    names = [f"b{i:02d}" for i in range(n)]
    t = {"transition_id": "cut_all", "actor": "attacker", "capability_tier": "C1", "duration_s": 0.001, "cost_ops": 1,
         "preconditions": True, "effects": [{"op": "set", "path": f"/facts/{v}", "value": False} for v in names],
         "adversarially_induced": True, "causal_parent_ids": []}
    return base_scenario({v: True for v in names}, {"any": [{"predicate": v} for v in names]}, [t],
                         {f"q:{v}": 1.0 for v in names}, {v: f"q:{v}" for v in names})


def goal_state(s, deadline):
    e = dcji.enumerate_attack_goals(s, deadline, "C1", "balanced")
    assert e["goals"], e
    st = copy.deepcopy(e["goals"][0]["state"]); st["timestamp_s"] = deadline
    return st


class FailClosed(unittest.TestCase):
    def test_i_fail_open_zero_cost_path(self):
        s = fail_open_scenario()
        plan = dcji.derive_critical_predicates(s, 2.0, "C1", "balanced")
        self.assertEqual(plan["status"], "NO_SELECTIVE_CERTIFICATE")
        cs = goal_state(s, 2.0)
        truth = commit_components(s, cs, "balanced")
        self.assertTrue(truth["attack_goal"])
        d = defenses.B6_dcji_gate(s, s["initial_state"], cs, "balanced", deadline_s=2.0, attacker_capability="C1")
        if PATCHED:
            self.assertEqual(d["decision"], "ABORT_REPLAN")
            self.assertEqual(d["defense_mode"], "dcji_fail_closed_abort")
            self.assertFalse(plan["full_refresh_rejects_all_goals"])
        else:
            # Locked core: FullRefresh fallback EXECUTEs an attack-goal state (fail-open, C1).
            self.assertEqual(d["decision"], "EXECUTE")
            self.assertEqual(d["defense_mode"], "dcji_fallback_full_refresh")

    @unittest.skipUnless(PATCHED, "patch-only")
    def test_i_b_fallback_kept_when_fullrefresh_proven(self):
        # Subset cap hit, but FullRefresh provably rejects every goal -> keep locked fallback.
        s = wide_or_scenario(6)
        cs = goal_state(s, 0.01)
        d = defenses.B6_dcji_gate(s, s["initial_state"], cs, "balanced", deadline_s=0.01,
                                  attacker_capability="C1", max_subset_evals=3)
        self.assertEqual(d["defense_mode"], "dcji_fallback_full_refresh")
        self.assertEqual(d["decision"], "ABORT_REPLAN")

    @unittest.skipUnless(PATCHED, "patch-only")
    def test_i_c_enumeration_timeout_fails_closed(self):
        s = wide_or_scenario(4)
        cs = goal_state(s, 0.01)
        d = defenses.B6_dcji_gate(s, s["initial_state"], cs, "balanced", deadline_s=0.01,
                                  attacker_capability="C1", max_expansions=1)
        self.assertEqual(d["decision"], "ABORT_REPLAN")
        self.assertEqual(d["defense_mode"], "dcji_fail_closed_abort")

    @unittest.skipUnless(PATCHED, "patch-only")
    def test_validator(self):
        s = fail_open_scenario()
        v = dcji.zero_cost_observability_violations(s, "balanced")
        self.assertEqual([x["transition_id"] for x in v], ["flip_x"])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            dcji.validate_refresh_observability(s, "balanced", mode="warn")
            self.assertTrue(any("zero-cost" in str(x.message) for x in w))
        with self.assertRaises(ValueError):
            dcji.validate_refresh_observability(s, "balanced", mode="reject")
        cs = goal_state(s, 2.0)
        d = defenses.B6_dcji_gate(s, s["initial_state"], cs, "balanced", deadline_s=2.0,
                                  attacker_capability="C1", observability_check="reject")
        self.assertEqual(d["decision"], "ABORT_REPLAN")
        self.assertEqual(dcji.zero_cost_observability_violations(wide_or_scenario(3), "balanced"), [])


@unittest.skipUnless(PATCHED, "patch-only")
class CapBound(unittest.TestCase):
    def test_ii_n24_cap1000_bounded_and_fail_closed(self):
        s = wide_or_scenario(24)
        tracemalloc.start()
        t0 = time.perf_counter()
        plan = dcji.derive_critical_predicates(s, 0.01, "C1", "balanced", max_subset_evals=1000)
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(plan["status"], "UNKNOWN_SUBSET_TIMEOUT")
        self.assertEqual(plan["subset_evals"], 1000)
        self.assertEqual(len(plan["candidate_query_ids"]), 24)
        self.assertLess(dt, 5.0)               # locked code would materialise 2^24 = 16.7M subsets first
        self.assertLess(peak, 64 * 2 ** 20)    # < 64 MiB traced
        cs = goal_state(s, 0.01)
        d = defenses.B6_dcji_gate(s, s["initial_state"], cs, "balanced", deadline_s=0.01,
                                  attacker_capability="C1", max_subset_evals=1000, critical_plan=plan)
        self.assertEqual(d["decision"], "ABORT_REPLAN")   # fail closed (FullRefresh proven to reject)
        # and the same when FullRefresh cannot be proven: add a zero-cost-only basis flip
        s2 = copy.deepcopy(s)
        s2["transitions"][0]["effects"].append({"op": "set", "path": "/facts/z", "value": False})
        s2["initial_state"]["facts"]["z"] = True
        s2["predicates"]["z"] = {"path": "/facts/z", "op": "eq", "value": True}
        # J = z OR (b00 OR ... OR b23); z is observable only via a zero-cost query, so no
        # refresh set (incl. FullRefresh) rejects the goal -> must not fall back to FullRefresh.
        s2["policy_profiles"]["balanced"]["justification"] = {"any": [{"predicate": "z"}, s["policy_profiles"]["balanced"]["justification"]]}
        s2["refresh_model"]["query_costs"]["imm:z"] = 0.0
        s2["refresh_model"]["predicate_query_ids"]["z"] = "imm:z"
        p2 = dcji.derive_critical_predicates(s2, 0.01, "C1", "balanced", max_subset_evals=1000)
        self.assertEqual(p2["status"], "UNKNOWN_SUBSET_TIMEOUT")
        self.assertFalse(p2["full_refresh_rejects_all_goals"])
        d2 =defenses.B6_dcji_gate(s2, s2["initial_state"], goal_state(s2, 0.01), "balanced", deadline_s=0.01,
                                   attacker_capability="C1", critical_plan=p2)
        self.assertEqual(d2["decision"], "ABORT_REPLAN")
        self.assertEqual(d2["defense_mode"], "dcji_fail_closed_abort")


def locked_reference_derive(scenario, deadline_s, cap, profile, max_subset_evals=100000):
    """Verbatim re-statement of the LOCKED exhaustive algorithm (dcji.py v8.1.1 lines 128-162),
    using float costs summed over a set, all subsets materialised and sorted."""
    enum = dcji.enumerate_attack_goals(scenario, deadline_s, cap, profile)
    if not enum["goals"]:
        return ("NOT_EXPLOITABLE", [], 0.0, 0)
    initial = scenario["initial_state"]
    cand = set()
    for g in enum["goals"]:
        cand.update(dcji.changed_material_queries(scenario, initial, g["state"], profile))
    cands = sorted(cand)
    qc = lambda combo: sum(float(scenario["refresh_model"]["query_costs"][q]) for q in set(combo))
    combos = sorted(((qc(c), len(c), c) for r in range(len(cands) + 1) for c in itertools.combinations(cands, r)),
                    key=lambda x: (x[0], x[1], x[2]))
    evals = 0
    for cost, _, combo in combos:
        evals += 1
        if evals > max_subset_evals:
            return ("UNKNOWN_SUBSET_TIMEOUT", None, None, evals - 1)
        if all(not commit_components(scenario, refresh_queries(scenario, initial, g["state"], combo, profile)[0], profile)["valid"]
               for g in enum["goals"]):
            return ("EXPLOITABLE", list(combo), float(cost), evals)
    return ("NO_SELECTIVE_CERTIFICATE", None, None, evals)


def random_expr(rng, names, depth=0):
    r = rng.random()
    if depth >= 2 or r < 0.35:
        e = {"predicate": rng.choice(names)}
        return {"not": e} if rng.random() < 0.1 else e
    k = "all" if r < 0.7 else "any"
    return {k: [random_expr(rng, names, depth + 1) for _ in range(rng.randint(2, 3))]}


def random_scenario(rng, cost_pool):
    n = rng.randint(1, 7)
    names = [f"v{i}" for i in range(n)]
    facts = {v: True for v in names}
    trans = []
    for i in range(rng.randint(1, 4)):
        eff = [{"op": "set", "path": f"/facts/{v}", "value": rng.random() < 0.2} for v in rng.sample(names, rng.randint(1, min(2, n)))]
        trans.append({"transition_id": f"t{i}", "actor": "attacker", "capability_tier": "C1",
                      "duration_s": rng.choice([0.5, 1.0, 1.5]), "cost_ops": rng.randint(1, 3), "preconditions": True,
                      "effects": eff, "adversarially_induced": True,
                      "causal_parent_ids": [f"t{i-1}"] if i and rng.random() < 0.2 else []})
    # queries: sometimes two predicates share a query
    qids = {}
    for v in names:
        qids[v] = f"q:{rng.choice(names)}" if rng.random() < 0.15 else f"q:{v}"
    costs = {q: rng.choice(cost_pool) for q in set(qids.values())}
    j = random_expr(rng, names)
    b = {"predicate": rng.choice(names)} if rng.random() < 0.3 else True
    return base_scenario(facts, j, trans, costs, qids, binding=b)


@unittest.skipUnless(PATCHED, "patch-only")
class LazyEquivalence(unittest.TestCase):
    def test_iii_a_generator_order_equals_exhaustive_sort(self):
        rng = random.Random(20260926)
        pool = [Fraction(1), Fraction(2), Fraction(3), Fraction(1, 2), Fraction(1, 10), Fraction(3, 10), Fraction(0)]
        for _ in range(600):
            n = rng.randint(0, 9)
            ids = [f"q{rng.randint(0, 999):03d}" for _ in range(n)]
            ids = sorted(set(ids))
            cost = {q: rng.choice(pool) for q in ids}
            lazy = list(dcji.iter_subsets_in_certificate_order(ids, cost))
            ref = sorted(((sum((cost[q] for q in c), Fraction(0)), len(c), c)
                          for r in range(len(ids) + 1) for c in itertools.combinations(ids, r)))
            self.assertEqual([(c, x) for c, x in lazy], [(c, x) for c, _, x in ref])

    def test_iii_b_derive_equals_locked_reference_on_random_scenarios(self):
        rng = random.Random(7)
        dyadic = [0.5, 1.0, 1.0, 2.0, 3.0, 4.0]
        checked = exploitable = multi_goal = 0
        for _ in range(600):
            s = random_scenario(rng, dyadic)
            try:
                if not commit_components(s, s["initial_state"], "balanced")["valid"]:
                    continue
            except Exception:
                continue
            ref = locked_reference_derive(s, 3.0, "C1", "balanced")
            p = dcji.derive_critical_predicates(s, 3.0, "C1", "balanced")
            got = (p["status"], p["critical_query_ids"], p["query_cost_units"], p["subset_evals"])
            self.assertEqual(got, ref, json.dumps(s)[:2000])
            checked += 1
            exploitable += ref[0] == "EXPLOITABLE"
            multi_goal += p["goal_count"] > 1
        self.assertGreaterEqual(checked, 500)
        self.assertGreater(exploitable, 100)
        self.assertGreater(multi_goal, 50)
        print(f"\n[iii-b] random scenarios checked={checked} exploitable={exploitable} multi_goal={multi_goal}")

    def test_iii_c_small_caps_equal(self):
        rng = random.Random(99)
        for _ in range(200):
            s = random_scenario(rng, [1.0, 2.0, 0.5])
            if not commit_components(s, s["initial_state"], "balanced")["valid"]:
                continue
            cap = rng.randint(1, 6)
            ref = locked_reference_derive(s, 3.0, "C1", "balanced", max_subset_evals=cap)
            p = dcji.derive_critical_predicates(s, 3.0, "C1", "balanced", max_subset_evals=cap)
            self.assertEqual((p["status"], p["critical_query_ids"], p["query_cost_units"], p["subset_evals"]), ref)


if __name__ == "__main__":
    unittest.main()
