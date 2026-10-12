#!/usr/bin/env python3
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--raw',required=True); p.add_argument('--expected',type=int,default=18); a=p.parse_args()
rows=[json.loads(x) for x in Path(a.raw).read_text().splitlines() if x.strip()]
arr=[x for r in rows for x in (r.get('writer_arrivals') or [])]
checks={'record_count':len(rows)==a.expected,'no_infra_error':all(r.get('status')!='INFRA_ERROR' for r in rows),
        'benign_writer_executed':any(x.get('http_status')==200 for x in arr),
        'writer_targets_seq_only':all((x.get('object')=='e38.seq') for x in arr),
        'all_rv409_have_validity_check':all((r.get('post_action_validation') or {}).get('all_target_validity_annotations_true') is True
                                           for r in rows if (r.get('delete') or {}).get('http_status')==409)}
print(json.dumps({'checks':checks,'PASS':all(checks.values()),'n':len(rows),'writer_arrivals':len(arr)},indent=2))
raise SystemExit(0 if all(checks.values()) else 4)
