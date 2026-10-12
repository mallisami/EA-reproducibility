#!/usr/bin/env python3
"""Tiny JSON viewer: jq.py FILE PYEXPR   (d = loaded JSON; prints the expression's value)."""
import json, sys
d = json.load(open(sys.argv[1]))
v = eval(sys.argv[2], {'d': d, 'json': json})
print(json.dumps(v, indent=1, default=str) if not isinstance(v, str) else v)
