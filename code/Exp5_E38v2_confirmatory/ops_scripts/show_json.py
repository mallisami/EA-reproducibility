#!/usr/bin/env python3
"""show_json.py FILE [key[.key...]] [-drop k1,k2] : pretty-print a JSON sub-tree"""
import json, sys
d = json.load(open(sys.argv[1]))
args = sys.argv[2:]
drop = []
if '-drop' in args:
    i = args.index('-drop')
    drop = args[i + 1].split(',')
    args = args[:i] + args[i + 2:]
if args:
    for k in args[0].split('.'):
        d = d[int(k)] if isinstance(d, list) else d[k]
if isinstance(d, dict):
    for k in drop:
        d.pop(k, None)
print(json.dumps(d, indent=1))
