from pathlib import Path
import json,hashlib,sys,subprocess
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):return json.loads(Path(p).read_text())
fail=[]
# frozen inputs
for rel,h in load(ROOT/"FROZEN_INPUTS.sha256.json").items():
 p=ROOT/rel
 if not p.exists() or sha(p)!=h:fail.append("frozen input mismatch "+rel)
# stale active statistics prohibited
if (ROOT/"analysis/confirmatory_comparisons.json").exists():fail.append("superseded confirmatory file still active")
if (ROOT/"analysis/E1_E5_HELDOUT_RESULTS.md").exists():fail.append("superseded E1/E5 report still active")
cc=load(ROOT/"analysis/confirmatory_comparisons_cluster_corrected.json")
if cc["C4"]["cluster_aware_noninferiority_established"] is not False:fail.append("C4 NI boundary wrong")
if abs(cc["C5"]["template_level_wilcoxon_less_p"]-0.004826937407846361)>1e-15:fail.append("C5 p drift")
if fail:
 print("FINAL STATIC AUDIT FAILED");[print(" -",x) for x in fail];sys.exit(1)
print("PASS: frozen inputs, authoritative statistics, and supersession rules are intact")
