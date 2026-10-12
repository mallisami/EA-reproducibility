#!/usr/bin/env bash
W=$HOME/e40_20260926; cd $W; R=results/a20260926
PY=$HOME/e39_20260926/venv_analysis/bin/python
head -12 ops/collect_main.log
grep -i 'lock\|refus\|gate' $R/analysis_stdout.log | head -5
nice $PY code/analysis/recompute_independent_e39.py --raw $R/RAW_TRIALS.jsonl --audit $R/audit.log --schedule $R/SCHEDULE.json \
   --compare $R/analysis/results_E39.json --out $R/analysis/independent_E40.json > $R/independent_stdout.log 2>&1; echo "independent rc=$?"
tail -15 $R/independent_stdout.log
E39C=$(ls $HOME/e39_20260926/results/e39-20260926t1515z/analysis*/cells_E39.csv 2>/dev/null | head -1)
echo "E39 cells: ${E39C:-none available}"
nice $PY e40/e40_supplement.py --run-dir $R --analysis-dir $R/analysis --out $R/supplement \
   --ref-e38v2r $HOME/e38v2r_20260926/results/e38v2r-20260926t1032z/analysis_final/cells_E38v2.csv ${E39C:+--ref-e39 $E39C} > $R/supplement_stdout.log 2>&1; echo "supp rc=$?"
nice python3 e40/smoke_gate.py $R/analysis/oracle_trials_E39.csv $R/RAW_TRIALS.jsonl > $R/QUALITY_GATES_MAIN.json; grep -E 'PASS|infra_rate|classification_rate|controls_bad' $R/QUALITY_GATES_MAIN.json
nice python3 e40/headline_e40.py $R > /dev/null; cat $R/HEADLINE_E40.txt
python3 -c "import json;r=json.load(open('$R/analysis/results_E39.json'));print(json.dumps(r.get('completeness_gate'))[:600])"
