#!/usr/bin/env python3
"""Descriptive E38 raw-trial audit. Not a statistical guarantee or E38 results without files."""
from __future__ import annotations
import argparse,csv,hashlib,json,statistics,sys
from collections import Counter,defaultdict
from pathlib import Path

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def numeric(rows,key):
    vals=[]
    for r in rows:
        x=r.get('guard',{}).get(key) if key in ('guard_get_count','core_evidence_get_count','refresh_latency_ms','gate_latency_ms','response_body_bytes') else r.get(key)
        if isinstance(x,(int,float)) and type(x) is not bool:vals.append(float(x))
    return {'n':len(vals),'mean':statistics.mean(vals) if vals else None,
            'median':statistics.median(vals) if vals else None}

def analyze(run):
    raw=run/'RAW_TRIALS.jsonl';session=json.loads((run/'SESSION.json').read_text());rows=[]
    for index,line in enumerate(raw.read_text().splitlines(),start=1):
        try:r=json.loads(line)
        except Exception as exc:raise ValueError('Corrupt raw JSON line '+str(index)) from exc
        rows.append(r)
    ids=[r['trial_id'] for r in rows]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate trial IDs; stop')
    expected={(cond,method,block,rep)
              for cond in session['conditions'] for method in session['methods']
              for block in range(session['blocks']) for rep in range(session['repetitions'])}
    observed=Counter((r.get('condition'),r.get('method'),r.get('block_id'),r.get('rep_index')) for r in rows)
    missing=sorted(expected-set(observed))
    unexpected_cells=sorted(set(observed)-expected, key=str)
    repeated=[str(k) for k,v in observed.items() if v!=1]
    by=defaultdict(list)
    for r in rows:by[(r['condition'],r['method'])].append(r)
    stats=[];unexpected=[]
    for (condition,method),rr in sorted(by.items()):
        statuses=Counter(r.get('status') for r in rr)
        mismatches=sum(r.get('guard_decision') not in (r.get('oracle_gate_decision'),
                 'UNKNOWN_COHERENCE') for r in rr if r.get('oracle_gate_decision') is not None)
        changed_after=sum(r.get('oracle_at_use',{}).get('decision')=='ABORT_REPLAN' and
                          r.get('server_http_status') in (200,202) for r in rr)
        unsafe_dispatch=sum(bool(r.get('delete_sent')) and r.get('guard_decision')!='EXECUTE' for r in rr)
        accepted_409=sum(r.get('server_http_status')==409 and bool(r.get('effect',{}).get('effect_verified')) for r in rr)
        subset={'condition':condition,'method':method,'planned_observed':len(rr),
          'errors':sum(bool(r.get('failed')) for r in rr),'unknown':statuses['UNKNOWN'],
          'complete':statuses['COMPLETE'],'complete_with_safety_note':statuses['COMPLETE_WITH_SAFETY_NOTE'],
          'initial_gate_mismatch':mismatches,'accepted_after_use_time_invalid':changed_after,
          'dispatch_after_abort':unsafe_dispatch,'409_counted_as_effect':accepted_409,
          'verified_effects':sum(bool(r.get('effect',{}).get('effect_verified')) for r in rr),
          'server_409':sum(r.get('server_http_status')==409 for r in rr),
          'GET_total':numeric(rr,'guard_get_count'),'GET_evidence':numeric(rr,'core_evidence_get_count'),
          'refresh_ms':numeric(rr,'refresh_latency_ms'),'gate_ms':numeric(rr,'gate_latency_ms'),
          'first_effect_ms':numeric(rr,'decision_to_first_effect_ms')}
        if unsafe_dispatch or accepted_409:unexpected.append(subset)
        stats.append(subset)
    # Distinguish matched comparisons from mixing dissimilar conditions.
    paired=defaultdict(dict)
    for r in rows:paired[(r['condition'],r['block_id'],r.get('rep_index'),r['namespace'])][r['method']]=r
    differences=defaultdict(list)
    incomplete=[]
    compare_both={'DCJI','FullRefresh'}.issubset(session['methods'])
    for key,item in sorted(paired.items()):
        if not compare_both:continue  # diagnostic single/partial-invariant campaign, not paired DCJI-vs-FullRefresh
        if not {'DCJI','FullRefresh'}.issubset(item):
            incomplete.append(key);continue
        d,f=item['DCJI'],item['FullRefresh']
        x=d.get('guard',{}).get('refresh_latency_ms');y=f.get('guard',{}).get('refresh_latency_ms')
        if x is not None and y is not None and not d.get('failed') and not f.get('failed'):
            differences[key[0]].append(x-y)
    comparisons=[]
    for cond,values in sorted(differences.items()):
        comparisons.append({'condition':cond,'pairs_complete':len(values),
          'mean_DCJI_minus_FullRefresh_ms':statistics.mean(values),
          'median_DCJI_minus_FullRefresh_ms':statistics.median(values),
          'individual_reversals':sum(v>0 for v in values),
          'positive_means_slower_DCJI':True})
    done=(len(rows)==session['total_planned_trials'] and not missing and
          not unexpected_cells and not repeated and not incomplete)
    output={'phase':session['phase'],'planned':session['total_planned_trials'],
       'observed':len(rows),'complete_schedule':done,'raw_sha256':sha(raw),
       'missing_scheduled_cells':len(missing),'unexpected_cells':len(unexpected_cells),
       'repeated_cells':repeated,'missing_cell_examples':[str(x) for x in missing[:12]],
       'unexpected_cell_examples':[str(x) for x in unexpected_cells[:12]],
       'failed_attempts':sum(bool(r.get('failed')) for r in rows),
       'includes_all_failures':True,'operator_review_required':True,
       'conditions':stats,'paired_refresh':comparisons,'incomplete_pairs':len(incomplete),
       'critical_accounting_defects':unexpected,'interpretation':
       'Descriptive one-host results only; non-primary stress and diagnostic baselines must not be pooled. '
       'No causal safety conclusion for uninstrumented post-gate evidence/scope changes.'}
    (run/'DESCRIPTIVE_AUDIT.json').write_text(json.dumps(output,indent=2)+'\n')
    cols=['condition','method','planned_observed','errors','unknown','complete','complete_with_safety_note',
      'initial_gate_mismatch','accepted_after_use_time_invalid','dispatch_after_abort','409_counted_as_effect',
      'verified_effects','server_409']
    with (run/'CELL_COUNTS.csv').open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=cols);writer.writeheader()
        for entry in stats:writer.writerow({k:entry[k] for k in cols})
    print(f'{session["phase"]}: {len(rows)}/{session["total_planned_trials"]} rows; '
          f'{len(unexpected)} cells with critical accounting defects; {len(incomplete)} incomplete pairs; '
          f'{len(missing)} missing, {len(unexpected_cells)} unexpected and {len(repeated)} repeated cells')
    return output
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('run_dir',type=Path);opts=a.parse_args()
    result=analyze(opts.run_dir)
    if not result['complete_schedule'] or result['critical_accounting_defects'] or result['failed_attempts']:
        raise SystemExit('FAILED: incomplete/failed/inconsistent attempt accounting; preserve the raw files')
