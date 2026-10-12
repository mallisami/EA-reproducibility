
import unittest
from pathlib import Path
from flashir_core.io import load_json
from flashir_core.engine import replay_path
from flashir_core.expr import commit_components
from flashir_core.dcji import search_dcji, enumerate_attack_goals, derive_critical_predicates
from flashir_core.defenses import B2_ttlguard,B4_fullrefresh,B5_nativeprecondition,B6_dcji_gate

ROOT=Path(__file__).resolve().parents[1]
DEV=ROOT/"scenarios"/"dev"

class Audit221(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ss=[load_json(p) for p in sorted(DEV.glob("*.json"))]

    def test_worlds(self):
        for s in self.ss:
            self.assertTrue(commit_components(s,s["initial_state"],s["primary_policy"])["valid"])
            for wn in ("W0","W1","W2","W3"):
                w=s["worlds"][wn]; r=replay_path(s,w["transition_path"],w["commit_deadline_s"])
                c=commit_components(s,r["state"],s["primary_policy"])
                self.assertEqual(c["valid"],w["expected_commit"]=="EXECUTE")
                if wn=="W2": self.assertTrue(c["attack_goal"])

    def test_dcji_w2_w3(self):
        for s in self.ss:
            w=s["worlds"]["W2"]
            self.assertEqual(search_dcji(s,w["commit_deadline_s"],s["attacker_capability_tier"],s["primary_policy"])["status"],"EXPLOITABLE")
            w=s["worlds"]["W3"]
            self.assertEqual(search_dcji(s,w["commit_deadline_s"],s["attacker_capability_tier"],s["primary_policy"])["status"],"NOT_EXPLOITABLE")

    def test_b6_safe_and_selective(self):
        strict=0
        for s in self.ss:
            w=s["worlds"]["W2"]; r=replay_path(s,w["transition_path"],w["commit_deadline_s"])
            b4=B4_fullrefresh(s,s["initial_state"],r["state"],s["primary_policy"])
            b6=B6_dcji_gate(s,s["initial_state"],r["state"],s["primary_policy"],deadline_s=w["commit_deadline_s"],
                            attacker_capability=s["attacker_capability_tier"])
            self.assertEqual(b6["decision"],"ABORT_REPLAN")
            self.assertLessEqual(b6["query_cost_units"],b4["query_cost_units"])
            strict += int(b6["query_cost_units"]<b4["query_cost_units"])
            for wn in ("W0","W1","W3"):
                ww=s["worlds"][wn]; rr=replay_path(s,ww["transition_path"],ww["commit_deadline_s"])
                bx=B6_dcji_gate(s,s["initial_state"],rr["state"],s["primary_policy"],deadline_s=ww["commit_deadline_s"],
                                attacker_capability=s["attacker_capability_tier"])
                self.assertEqual(bx["decision"],"EXECUTE",f"{s['scenario_id']} {wn}")
        self.assertGreater(strict,0)

    def test_post_goal_continuation(self):
        s={
            "attacker_capability_tier":"C1",
            "initial_state":{"timestamp_s":0,"facts":{"auth":True,"a":True,"b":True},"resources":[],"processes":[],
                             "network_bindings":[],"sessions":[],"context":{},"evidence":[],"queue":[]},
            "predicates":{
                "p_auth":{"path":"/facts/auth","op":"eq","value":True},
                "p_a":{"path":"/facts/a","op":"eq","value":True},
                "p_b":{"path":"/facts/b","op":"eq","value":True}},
            "action":{"authorization":{"predicate":"p_auth"},"execution_precondition":True},
            "policy_profiles":{"balanced":{"justification":{"all":[{"predicate":"p_a"},{"predicate":"p_b"}]},"binding":True,"scope":True}},
            "transitions":[
                {"transition_id":"t1","actor":"attacker","capability_tier":"C1","duration_s":1,"cost_ops":1,
                 "preconditions":True,"effects":[{"op":"set","path":"/facts/a","value":False}],
                 "adversarially_induced":True,"causal_parent_ids":[]},
                {"transition_id":"t2","actor":"attacker","capability_tier":"C1","duration_s":1,"cost_ops":1,
                 "preconditions":{"not":{"predicate":"p_a"}},"effects":[{"op":"set","path":"/facts/a","value":True},
                 {"op":"set","path":"/facts/b","value":False}],"adversarially_induced":True,"causal_parent_ids":["t1"]}],
            "refresh_model":{"cost_unit":"normalized_query_unit","query_costs":{"qa":1,"qb":1},
                             "predicate_query_ids":{"p_a":"qa","p_b":"qb"},"query_observed_at_s":{"qa":0,"qb":0}}
        }
        e=enumerate_attack_goals(s,3,"C1","balanced")
        self.assertEqual(len(e["goals"]),2,e)
        p=derive_critical_predicates(s,3,"C1","balanced")
        self.assertEqual(set(p["critical_query_ids"]),{"qa","qb"})

    def test_shared_query(self):
        s={
            "attacker_capability_tier":"C1",
            "initial_state":{"timestamp_s":0,"facts":{"auth":True,"x":1},"resources":[],"processes":[],
                             "network_bindings":[],"sessions":[],"context":{},"evidence":[],"queue":[]},
            "predicates":{"p_auth":{"path":"/facts/auth","op":"eq","value":True},
                          "p1":{"path":"/facts/x","op":"eq","value":1},
                          "p2":{"path":"/facts/x","op":"ne","value":0}},
            "action":{"authorization":{"predicate":"p_auth"},"execution_precondition":True},
            "policy_profiles":{"balanced":{"justification":{"all":[{"predicate":"p1"},{"predicate":"p2"}]},"binding":True,"scope":True}},
            "transitions":[{"transition_id":"t","actor":"attacker","capability_tier":"C1","duration_s":1,"cost_ops":1,
                            "preconditions":True,"effects":[{"op":"set","path":"/facts/x","value":0}],
                            "adversarially_induced":True,"causal_parent_ids":[]}],
            "refresh_model":{"cost_unit":"normalized_query_unit","query_costs":{"qx":1},
                             "predicate_query_ids":{"p1":"qx","p2":"qx"},"query_observed_at_s":{"qx":0}}
        }
        p=derive_critical_predicates(s,2,"C1","balanced")
        self.assertEqual(p["critical_query_ids"],["qx"]); self.assertEqual(p["query_cost_units"],1)

    def test_all_policy_profiles_f51(self):
        s=next(x for x in self.ss if x["scenario_id"]=="F5.1"); d=s["worlds"]["W2"]["commit_deadline_s"]
        self.assertEqual(search_dcji(s,d,"C1","balanced")["status"],"EXPLOITABLE")
        self.assertEqual(search_dcji(s,d,"C1","conservative")["status"],"NOT_EXPLOITABLE")
        self.assertEqual(search_dcji(s,d,"C1","aggressive")["status"],"NOT_EXPLOITABLE")

    def test_b5_atomic(self):
        s=next(x for x in self.ss if x["scenario_id"]=="F1.1"); w=s["worlds"]["W2"]
        r=replay_path(s,w["transition_path"],w["commit_deadline_s"])
        b=B5_nativeprecondition(s,s["initial_state"],r["state"],s["primary_policy"])
        self.assertEqual(b["decision"],"ABORT_REPLAN"); self.assertEqual(b["query_cost_units"],0.0)

    def test_ttl_extremes(self):
        for s in self.ss:
            w=s["worlds"]["W2"]; r=replay_path(s,w["transition_path"],w["commit_deadline_s"])
            z=B2_ttlguard(s,s["initial_state"],r["state"],s["primary_policy"],ttl_s=0)
            f=B4_fullrefresh(s,s["initial_state"],r["state"],s["primary_policy"])
            self.assertEqual((z["decision"],z["query_cost_units"]),(f["decision"],f["query_cost_units"]))
            h=B2_ttlguard(s,s["initial_state"],r["state"],s["primary_policy"],ttl_s=10**9)
            self.assertEqual((h["decision"],h["query_cost_units"]),("EXECUTE",0))

    def test_subset_cap_fallback(self):
        s=next(x for x in self.ss if x["scenario_id"]=="F5.5"); w=s["worlds"]["W2"]
        r=replay_path(s,w["transition_path"],w["commit_deadline_s"])
        p=derive_critical_predicates(s,w["commit_deadline_s"],"C2","balanced",max_subset_evals=1)
        self.assertEqual(p["status"],"UNKNOWN_SUBSET_TIMEOUT")
        b=B6_dcji_gate(s,s["initial_state"],r["state"],"balanced",deadline_s=w["commit_deadline_s"],
                       attacker_capability="C2",max_subset_evals=1)
        self.assertEqual(b["defense_mode"],"dcji_fallback_full_refresh")
        self.assertEqual(b["decision"],"ABORT_REPLAN")

if __name__=="__main__": unittest.main()
