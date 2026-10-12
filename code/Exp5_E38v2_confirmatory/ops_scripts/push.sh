#!/bin/bash
# unpack a tarball of edited files (relative to the rerun tree) produced locally; LF only
set -euo pipefail
E=~/e38v2r_20260926
tar -xzf ${1:-/tmp/e38v2r_push.tgz} -C $E
find $E/src $E/analysis $E/protocol $E/deploy $E/webhook -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo pushed
