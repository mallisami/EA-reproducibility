#!/usr/bin/env python3
"""print audit events (stdin) whose objectRef.name == argv[1] (optionally namespace argv[2])"""
import json, sys
name = sys.argv[1]
ns = sys.argv[2] if len(sys.argv) > 2 else None
for l in sys.stdin:
    if name not in l:
        continue
    e = json.loads(l)
    o = e.get('objectRef') or {}
    if o.get('name') != name or (ns and o.get('namespace') != ns):
        continue
    print(e['stageTimestamp'], e['verb'], (e.get('responseStatus') or {}).get('code'), e['user']['username'],
          (e.get('impersonatedUser') or {}).get('username'), o.get('namespace'),
          ((e.get('responseStatus') or {}).get('message') or '')[:120])
