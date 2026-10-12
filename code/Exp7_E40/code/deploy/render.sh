#!/usr/bin/env bash
# Render the E39 manifest templates for one run id. Pure file operation: applies nothing.
#   usage: deploy/render.sh <RUN_ID> <OUTDIR>
# Produces <OUTDIR>/{namespace,rbac,webhook,webhook-config}.yaml with RUNID substituted
# (test namespace e40-<RUN_ID>; webhook-config.yaml still contains CA_BUNDLE_PLACEHOLDER until
# gen_certs.sh runs) and <OUTDIR>/RENDER_MANIFEST.txt with sha256 of templates and rendered files.
set -euo pipefail
RUN_ID="${1:?usage: render.sh <RUN_ID> <OUTDIR>}"
OUT="${2:?usage: render.sh <RUN_ID> <OUTDIR>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! [[ "$RUN_ID" =~ ^[a-z0-9]([-a-z0-9]{0,40}[a-z0-9])?$ ]]; then
  echo "RUN_ID must be a DNS label fragment [a-z0-9-], <=42 chars: '$RUN_ID'" >&2; exit 2
fi
case "$RUN_ID" in
  system|load) echo "RUN_ID '$RUN_ID' is reserved (e40-$RUN_ID is not a test namespace)" >&2; exit 2 ;;
esac
if [[ -e "$OUT" ]] && [[ -n "$(ls -A "$OUT" 2>/dev/null)" ]]; then
  echo "refusing to overwrite non-empty $OUT" >&2; exit 2
fi
mkdir -p "$OUT"
FILES=(namespace.yaml rbac.yaml webhook.yaml webhook-config.yaml)
{
  echo "run_id=$RUN_ID"
  echo "namespace=e40-$RUN_ID"
  echo "rendered_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/RENDER_MANIFEST.txt"
for f in "${FILES[@]}"; do
  sed "s/RUNID/${RUN_ID}/g" "$HERE/$f" > "$OUT/$f"
  if grep -q 'RUNID' "$OUT/$f"; then echo "unsubstituted RUNID in $f" >&2; exit 3; fi
  echo "template_sha256 $(sha256sum "$HERE/$f" | cut -d' ' -f1) $f" >> "$OUT/RENDER_MANIFEST.txt"
  echo "rendered_sha256 $(sha256sum "$OUT/$f" | cut -d' ' -f1) $f" >> "$OUT/RENDER_MANIFEST.txt"
done
echo "rendered ${FILES[*]} for e40-$RUN_ID into $OUT"
