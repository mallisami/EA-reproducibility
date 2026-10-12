#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?usage: $0 RUN_ID}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; RT="$ROOT/runtime/$RUN_ID"; [[ -d "$RT" ]] || { echo "missing $RT" >&2; exit 2; }
OUT="$ROOT/RETURN_A4D_${RUN_ID}.tar.gz"; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/runtime/$RUN_ID" "$TMP/protocol" "$TMP/environment"
# Runtime evidence only; omit tokens and generated private keys.
cp -a "$RT/results" "$TMP/runtime/$RUN_ID/"
if [[ -d "$RT/render" ]]; then
  mkdir -p "$TMP/runtime/$RUN_ID/render"
  find "$RT/render" -maxdepth 1 -type f -exec cp -a {} "$TMP/runtime/$RUN_ID/render/" \;
  if [[ -d "$RT/render/certs" ]]; then
    mkdir -p "$TMP/runtime/$RUN_ID/render/certs"
    find "$RT/render/certs" -maxdepth 1 -type f ! -name '*.key' -exec cp -a {} "$TMP/runtime/$RUN_ID/render/certs/" \;
  fi
fi
cp -a "$ROOT/runtime/environment/." "$TMP/environment/" 2>/dev/null || true
cp -a "$ROOT/A4D_CONFIRMATION/protocol/SCHEDULE_A4D_MAIN.json" "$ROOT/A4D_CONFIRMATION/protocol/SCHEDULE_A4D_SMOKE.json" "$ROOT/A4D_CONFIRMATION/protocol/LOCK_A4D_MAIN.json" "$ROOT/A4D_CONFIRMATION/protocol/LOCK_A4D_SMOKE.json" "$ROOT/A4D_CONFIRMATION/protocol/A4D_CONFIRMATION_PROTOCOL.md" "$TMP/protocol/"
# Hash payload files only. The manifest must not hash itself while shell
# redirection is still constructing it.
( cd "$TMP" && find . -type f ! -path './RETURN_MANIFEST.sha256' -print0 | sort -z | xargs -0 sha256sum > RETURN_MANIFEST.sha256 )
tar -C "$TMP" -czf "$OUT" .
sha256sum "$OUT" > "$OUT.sha256"
echo "$OUT"
echo "$OUT.sha256"
