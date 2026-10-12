#!/usr/bin/env bash
set -euo pipefail
OUT=${1:?usage: $0 OUT_FILE}
mkdir -p "$(dirname "$OUT")"
sudo python3 - <<'PY' > "$OUT"
from pathlib import Path
p=Path('/var/lib/rancher/k3s/server/logs')
for f in sorted(p.glob('audit.log*'), key=lambda x:x.stat().st_mtime):
    try: print(f.read_text(),end='')
    except UnicodeDecodeError: pass
PY
