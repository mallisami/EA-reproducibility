#!/bin/bash
cd ~/dcji_reanalysis_20260926/work/offline
O=../../out/offline/patch.diff
diff -ruN -x __pycache__ deterministic_orig/flashir_core deterministic_patched/flashir_core > $O
diff -u /dev/null deterministic_patched/tests/test_offline_patches.py \
  | sed '1s|.*|--- /dev/null|; 2s|.*|+++ deterministic_patched/tests/test_offline_patches.py (new file)|' >> $O
echo "diff lines: $(wc -l < $O)  added: $(grep -c '^+' $O)  removed: $(grep -c '^-' $O)"
grep '^---\|^+++' $O
# copy patched core + new tests for hand-back
mkdir -p ../../out/offline/patched_flashir_core ../../out/offline/new_tests ../../out/offline/scripts
cp deterministic_patched/flashir_core/*.py ../../out/offline/patched_flashir_core/
cp deterministic_patched/tests/test_offline_patches.py ../../out/offline/new_tests/
cp scripts/*.py scripts/*.sh ../../out/offline/scripts/
