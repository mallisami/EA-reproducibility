#!/bin/bash
# Extract the pushed work tarball over the workspace (code dirs only). Backs up once.
set -euo pipefail
E=~/e38v2_20260926
cd $E
if [ ! -f ~/e38v2_ops/backup_pre_integration.tgz ]; then
  tar -czf ~/e38v2_ops/backup_pre_integration.tgz --exclude=__pycache__ --exclude=.pytest_cache src webhook deploy protocol analysis
  sha256sum ~/e38v2_ops/backup_pre_integration.tgz
fi
tar -xzf ~/e38v2_ops/work.tgz -C $E
find $E/src $E/webhook $E/deploy $E/protocol $E/analysis -name '*.py' -o -name '*.sh' -o -name '*.txt' -o -name '*.json' -o -name '*.yaml' | xargs grep -l $'\r' 2>/dev/null && echo "CRLF FOUND" || echo "no CRLF"
chmod +x $E/deploy/*.sh $E/analysis/run_tests.sh
echo pushed
