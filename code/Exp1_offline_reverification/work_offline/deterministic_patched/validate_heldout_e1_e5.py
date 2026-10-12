
from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parent
def load(p): return json.loads(Path(p).read_text(encoding="utf-8"))
e1=load(ROOT/"results/heldout/e1_oracle_vulnerability.json")
e5=load(ROOT/"results/heldout/e5_defense_comparison.json")
fail=[]
if len(e1)!=384: fail.append(f"E1 row count {len(e1)} != 384")
if len(e5)!=1856: fail.append(f"E5 row count {len(e5)} != 1856")
pos=[r for r in e1 if r["world"]=="W2" and r["scenario_role"]=="positive_ea_toctou"]
if len(pos)!=92: fail.append(f"positive W2 count {len(pos)} != 92")
if not all(r["initial_action_correct"] and r["authorization_valid_at_commit"] for r in e1):
    fail.append("initial correctness or authorization invariant violated")
f56=[r for r in e1 if r["template_id"]=="F5.6" and r["world"]=="W2"]
if len(f56)!=4 or not all(r["valid_at_commit"] and not r["unsafe_commit"] for r in f56):
    fail.append("F5.6 negative control failed")
# No inapplicable B3/B5 rows.
for r in e5:
    if r["defense_id"]=="B3" and r["family"] not in ("F1","F2","F3"):
        fail.append(f"inapplicable B3 emitted for {r['instance_id']}")
    if r["defense_id"]=="B5" and r["family"]!="F1":
        fail.append(f"inapplicable B5 emitted for {r['instance_id']}")
# Frozen B2 TTL marker.
for r in e5:
    if r["defense_id"]=="B2" and r.get("defense_mode")!="ttl_2s":
        fail.append(f"B2 TTL drift: {r.get('defense_mode')}")
if fail:
    print("HELD-OUT RESULT VALIDATION FAILED")
    for f in fail: print(" -",f)
    sys.exit(1)
print("PASS: E1 row count and 92-positive/4-negative populations")
print("PASS: E5 baseline applicability and frozen TTL=2s")
print("PASS: authorization invariant and F5.6 negative control")
print("HELD-OUT E1/E5 RESULTS ARE INTERNALLY VALID.")
