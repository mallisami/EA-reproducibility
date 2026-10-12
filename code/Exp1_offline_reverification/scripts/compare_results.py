#!/usr/bin/env python3
"""Row-by-row semantic comparison of two result JSON files (list of dicts, or dict).

Keys whose name contains 'runtime' (wall-clock timings) are ignored; everything else
(decisions, costs, certificates, flags, paths, hashes) must be identical.
Usage: compare_results.py FROZEN.json NEW.json [label]
Exit code 0 iff semantically identical.
"""
import json, sys
from collections import Counter


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if "runtime" not in k and not k.startswith("_")}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def main():
    a = json.load(open(sys.argv[1]))
    b = json.load(open(sys.argv[2]))
    label = sys.argv[3] if len(sys.argv) > 3 else sys.argv[2]
    sa, sb = strip(a), strip(b)
    if isinstance(sa, list) and isinstance(sb, list):
        n = min(len(sa), len(sb))
        diff_rows = 0
        diff_keys = Counter()
        examples = []
        for i in range(n):
            if sa[i] != sb[i]:
                diff_rows += 1
                ks = set(sa[i]) | set(sb[i])
                for k in ks:
                    if sa[i].get(k, "<missing>") != sb[i].get(k, "<missing>"):
                        diff_keys[k] += 1
                        if len(examples) < 10:
                            examples.append((i, k, sa[i].get(k, "<missing>"), sb[i].get(k, "<missing>")))
        ok = diff_rows == 0 and len(sa) == len(sb)
        print(f"[{label}] rows frozen={len(sa)} new={len(sb)} differing_rows={diff_rows} "
              f"differing_keys={dict(diff_keys)} -> {'IDENTICAL' if ok else 'DIFFERENT'}")
        for e in examples:
            print("   example diff:", e)
        # decision summary
        if sa and isinstance(sa[0], dict) and "commit_decision" in sa[0]:
            c = Counter((r.get("defense_id"), r.get("world"), r.get("commit_decision"), r.get("unsafe_commit"), r.get("false_abort"))
                        for r in sb)
            print(f"   decision tally (new): {len(c)} distinct (defense,world,decision,unsafe,false_abort) cells")
        sys.exit(0 if ok else 1)
    ok = sa == sb
    print(f"[{label}] dict comparison -> {'IDENTICAL' if ok else 'DIFFERENT'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
