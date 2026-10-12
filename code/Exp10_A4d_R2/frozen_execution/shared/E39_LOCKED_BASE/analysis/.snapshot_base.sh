#!/bin/sh
# One-off: remove the dev scratch dir and snapshot the E38v2-rerun base code (read-only reference).
set -e
cd "$HOME/e39_20260926/analysis"
rm -rf _dev .pytest_tmp
mkdir -p base_e38v2r
cd base_e38v2r
for f in analysis/oracle.py analysis/e38v2_common.py analysis/analyze_e38v2.py analysis/stage2_topup.py \
         protocol/ANALYSIS_PLAN_E38v2.txt protocol/lock_hash.py; do
  b=$(basename "$f")
  [ -e "$b.orig" ] && chmod u+w "$b.orig"
  cp -p "$HOME/e38v2r_20260926/$f" "$b.orig"
done
[ -e BASE_SHA256.txt ] && chmod u+w BASE_SHA256.txt
sha256sum *.orig > BASE_SHA256.txt
echo "source: ~/e38v2r_20260926 (E38v2 confirmatory rerun, plan v1.2, oracle e38v2.oracle.v1.2); copied $(date -u +%FT%TZ); read-only reference, renamed *.orig so it is never imported or collected by pytest" >> BASE_SHA256.txt
chmod a-w *
ls -la
cat BASE_SHA256.txt
