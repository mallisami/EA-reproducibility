import json, sys, glob, os
W = os.path.expanduser("~/dcji_reanalysis_20260926")
sys.path.insert(0, W + "/work/offline/deterministic_orig")
from flashir_core.refresh import material_predicates
from flashir_core.engine import transition_allowed_for_attacker_model

files = sorted(glob.glob(W + "/inputs/frozen/02_locked_methodology_v8.1.1/deterministic/scenarios/heldout/*.json")) + \
        sorted(glob.glob(W + "/inputs/frozen/03_extended_study_v9/scenarios/pilot/*.json"))
for f in files:
    s = json.load(open(f))
    prof = s["primary_policy"]
    mats = sorted(material_predicates(s, prof))
    cap = s["attacker_capability_tier"]
    inm = [t["transition_id"] for t in s["transitions"] if transition_allowed_for_attacker_model(t, cap)]
    written = {e["path"] for t in s["transitions"] if t["transition_id"] in inm for e in t["effects"]}
    costs = s["refresh_model"]["query_costs"]
    zero = sorted(p for p in mats if float(costs[s["refresh_model"]["predicate_query_ids"][p]]) <= 0)
    unmod = sorted(p for p in mats if s["predicates"][p]["path"] not in written)
    auth_q = s["action"]["authorization"]["predicate"] in s["refresh_model"]["predicate_query_ids"]
    ws = {k: (v["commit_deadline_s"], len(v["transition_path"])) for k, v in s.get("worlds", {}).items()}
    print(os.path.basename(f), s["scenario_id"], s["family"], s.get("scenario_role"), "T=%d inmodel=%d" % (len(s["transitions"]), len(inm)),
          "Q=%d" % len(costs), "mats=%d" % len(mats), "zero=%s" % zero, "unmodeled=%s" % unmod, "authQ=%s" % auth_q,
          "worlds=%s" % ws, "execpre=%s" % (s["action"]["execution_precondition"] is not True))
