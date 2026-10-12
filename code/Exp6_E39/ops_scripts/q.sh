cd ~/e39_20260926/results/e39-20260926t1515z/analysis_final
tail -30 ~/e39_ops/final.log | grep -vE '^[A-G]/' | cut -c1-300
python3 - <<'PY'
import json
d = json.load(open('independent_E39.json'))
c = d.get('comparison') or {}
print('n_differences', c.get('n_differences'))
for x in c.get('differences') or []:
    print(json.dumps(x))
print({k: v for k, v in d.items() if k not in ('cells', 'comparison')})
PY
