import json,sys
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(sorted(rows[0].keys()))
for r in rows:
    g=r.get('guard',{})
    print(r['trial_id'][-3:],r['condition'],r['method'],r['status'],r.get('guard_decision'),r.get('oracle_gate_decision'),
          (r.get('oracle_at_use') or {}).get('decision'),r.get('server_http_status'),(r.get('effect') or {}).get('effect_verified'),
          g.get('refresh_latency_ms'),g.get('gate_latency_ms'),r.get('decision_to_first_effect_ms'),r.get('elapsed_wall_ms'),
          [e['kind'] for e in r['events']])
r=[x for x in rows if x['condition']=='postgate_evidence'][0]
print(json.dumps({k:v for k,v in r.items() if k not in('all_requests',)},default=str)[:6000])
for q in r['all_requests']:
    print(q['kind'],q['method'],q['path'].rsplit('/',1)[-1],q.get('status'),round(q['t0_ms'],3),round(q['t1_ms'],3))
