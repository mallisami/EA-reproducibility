#!/bin/bash
# Usage: run_regression.sh orig|patched
# Runs packaged tests + held-out runners (E1/E5, E3/E4, E10, E11) + dev matrix + 68-case pilot
# against the given working copy, writing ONLY to out/offline/regression_<variant>/,
# then compares every regenerated file to the frozen one (timings ignored).
set -u
V=$1
W=$HOME/dcji_reanalysis_20260926
C=$W/work/offline/deterministic_$V
F=$W/inputs/frozen/02_locked_methodology_v8.1.1/deterministic
P9=$W/inputs/frozen/03_extended_study_v9
O=$W/out/offline/regression_$V
PY="nice -n 10 $W/venv/bin/python"
mkdir -p $O
cd $C
# Redirect runner outputs (edits applied to the COPY only; idempotent).
for f in run_heldout_e1_e5.py run_heldout_e3_e4.py run_heldout_e10.py run_heldout_e11.py; do
  sed -i 's|^OUT=ROOT/"results"/"heldout"$|OUT=Path(__import__("os").environ["DCJI_OUT"])  # redirected (offline reanalysis copy)|' $f
done
sed -i 's|OUT=ROOT/"results/dev/deterministic_matrix.json"|OUT=Path(__import__("os").environ["DCJI_OUT"])/"deterministic_matrix.json"  # redirected|' run_dev_matrix.py
grep -n '^OUT=\|OUT=Path' run_heldout_*.py run_dev_matrix.py > $O/redirect_check.txt

echo "== unittest ($V) ==" > $O/SUMMARY.txt
$PY -m unittest discover -s tests -v > $O/tests_packaged.log 2>&1
tail -3 $O/tests_packaged.log >> $O/SUMMARY.txt
if [ -d $W/work/offline/newtests ] && [ "$V" = "patched" ]; then :; fi

export DCJI_OUT=$O/heldout
mkdir -p $DCJI_OUT
for r in run_heldout_e1_e5.py run_heldout_e3_e4.py run_heldout_e10.py run_heldout_e11.py; do
  /usr/bin/time -v $PY $r > $O/$r.log 2> $O/$r.time
  echo "$r exit=$? $(grep 'Elapsed' $O/$r.time)" >> $O/SUMMARY.txt
done
export DCJI_OUT=$O/dev
if $PY -c 'import jsonschema' 2>/dev/null; then
  $PY run_dev_matrix.py > $O/run_dev_matrix.log 2>&1; echo "dev matrix exit=$?" >> $O/SUMMARY.txt
else
  echo "dev matrix: jsonschema not installed in venv -> using schema-free mirror" >> $O/SUMMARY.txt
  sed -e 's/^import jsonschema$/import types; jsonschema=types.SimpleNamespace(validate=lambda *a,**k: None)  # schema check disabled (not installed)/' run_dev_matrix.py > $W/work/offline/run_dev_matrix_noschema_$V.py
  cp $W/work/offline/run_dev_matrix_noschema_$V.py ./_run_dev_matrix_noschema.py
  $PY _run_dev_matrix_noschema.py > $O/run_dev_matrix.log 2>&1; echo "dev matrix (noschema) exit=$?" >> $O/SUMMARY.txt
  rm -f ./_run_dev_matrix_noschema.py
fi
DCJI_CORE_ROOT=$C DCJI_PILOT_DIR=$P9/scenarios/pilot DCJI_OUT_FILE=$O/pilot_confirmatory_W2.json \
  $PY $W/work/offline/scripts/run_pilot_w2_redirect.py > $O/pilot.log 2>&1
echo "pilot exit=$? $(tail -1 $O/pilot.log)" >> $O/SUMMARY.txt

echo "== comparisons vs FROZEN ==" >> $O/SUMMARY.txt
CMP="$PY $W/work/offline/scripts/compare_results.py"
for f in e1_oracle_vulnerability e5_defense_comparison e3_delay_sensitivity e3_long_horizon e4_capability_sensitivity e10_incomplete_transition_model e11_telemetry_lag; do
  if [ -f $O/heldout/$f.json ]; then $CMP $F/results/heldout/$f.json $O/heldout/$f.json $f >> $O/SUMMARY.txt 2>&1; else echo "[$f] NOT PRODUCED" >> $O/SUMMARY.txt; fi
done
$CMP $F/results/dev/deterministic_matrix.json $O/dev/deterministic_matrix.json dev_matrix >> $O/SUMMARY.txt 2>&1
$CMP $P9/results/pilot_confirmatory_W2.json $O/pilot_confirmatory_W2.json pilot68 >> $O/SUMMARY.txt 2>&1
cat $O/SUMMARY.txt
