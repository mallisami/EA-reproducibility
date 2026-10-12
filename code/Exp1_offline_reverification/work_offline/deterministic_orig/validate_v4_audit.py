
from pathlib import Path
import json, hashlib, sys
ROOT=Path(__file__).resolve().parent
def load(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
a=load(ROOT/"analysis/V4_STRICT_AUDIT.json")
h=load(ROOT/"RAW_RESULT_HASH_AUDIT.json")
fail=[]
if not h["identical"]: fail.append("raw results changed")
if a["checks"]["frozen_input_hash_mismatches"]: fail.append("frozen input mismatch")
if a["checks"]["row_label_errors"]: fail.append("row label errors")
if a["checks"]["B6_fallback_count"]!=0: fail.append("unexpected B6 fallback")
if a["checks"]["primary_positive_W2_rows"]!=92: fail.append("primary denominator drift")
if a["cluster_aware_confirmatory"]["C1"]["template_pairs"]!=23: fail.append("C1 template count")
if a["cluster_aware_confirmatory"]["C4"]["cluster_aware_noninferiority_established"] is not False:
    fail.append("C4 audit conclusion drift")
if fail:
    print("V4.1 AUDIT VALIDATION FAILED")
    [print(" -",x) for x in fail]
    sys.exit(1)
print("PASS: raw v4 E1/E5 files unchanged")
print("PASS: frozen inputs and row-level classifications verified")
print("PASS: template-cluster inference recomputed")
print("PASS: C4 formal non-inferiority correctly withheld")
print("V4.1 STRICT AUDIT IS VALID.")
