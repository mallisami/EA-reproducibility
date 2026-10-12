#!/usr/bin/env bash
set -euo pipefail
ROLE=${1:?usage: $0 SERVER|WORKER PRIVATE_IP}
PRIVATE_IP=${2:?usage: $0 SERVER|WORKER PRIVATE_IP}
ROLE_UP=$(printf '%s' "$ROLE" | tr '[:lower:]' '[:upper:]')
case "$ROLE_UP" in SERVER|WORKER) ;; *) echo 'ROLE must be SERVER or WORKER' >&2; exit 2;; esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/runtime/environment"
mkdir -p "$OUT"
ROLE_LOW=$(printf '%s' "$ROLE_UP" | tr '[:upper:]' '[:lower:]')
TXT="$OUT/azure_${ROLE_LOW}_preflight.txt"
JSON="$OUT/azure_${ROLE_LOW}_metadata.json"

. /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || { echo "Expected Ubuntu 24.04; got ${ID:-unknown} ${VERSION_ID:-unknown}" >&2; exit 3; }

CPU=$(nproc)
MEM_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
FREE_KB=$(df -Pk "$ROOT" | awk 'NR==2{print $4}')
[[ "$CPU" -ge 2 ]] || { echo "Need >=2 vCPU; found $CPU" >&2; exit 3; }
[[ "$MEM_KB" -ge 3800000 ]] || { echo "Need >=4 GiB-class RAM; MemTotal=${MEM_KB} kB" >&2; exit 3; }
[[ "$FREE_KB" -ge 20971520 ]] || { echo "Need >=20 GiB free on package filesystem; free=${FREE_KB} kB" >&2; exit 3; }
ip -4 addr show | grep -Fq "$PRIVATE_IP" || { echo "Private IP $PRIVATE_IP is not assigned to this VM" >&2; ip -4 addr; exit 3; }

SYNC=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
if [[ "$SYNC" != yes ]]; then
  echo 'System clock is not reported NTP-synchronized. Fix time synchronization before the scientific run.' >&2
  exit 3
fi

command -v curl >/dev/null || { echo 'curl missing; run 00_prepare_ubuntu.sh' >&2; exit 3; }
curl -fsS --max-time 15 https://get.k3s.io >/dev/null || { echo 'Cannot reach https://get.k3s.io' >&2; exit 3; }

META_TMP=$(mktemp); trap 'rm -f "$META_TMP"' EXIT
if curl -fsS --max-time 3 -H Metadata:true \
  'http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01' > "$META_TMP"; then
  python3 - "$META_TMP" "$JSON" <<'PY'
import json,sys
src=json.load(open(sys.argv[1],encoding='utf-8'))
keep={k:src.get(k) for k in ('location','zone','vmSize','osType')}
img=src.get('storageProfile',{}).get('imageReference',{}) or {}
keep['imageReference']={k:img.get(k) for k in ('publisher','offer','sku','version')}
json.dump(keep,open(sys.argv[2],'w',encoding='utf-8'),indent=2,sort_keys=True); open(sys.argv[2],'a').write('\n')
PY
else
  printf '{"azure_imds":"unavailable"}\n' > "$JSON"
fi

VM_SIZE=$(python3 - "$JSON" <<'PY'
import json,sys
try: print((json.load(open(sys.argv[1])) or {}).get('vmSize') or '')
except Exception: print('')
PY
)
if [[ "$VM_SIZE" == Standard_B* ]]; then
  echo "WARNING: Azure burstable VM size detected ($VM_SIZE). Keep this recorded; absolute timing may be noisier." >&2
fi

{
  echo "timestamp_utc=$(date -u +%FT%TZ)"
  echo "role=$ROLE_UP"
  echo "private_ip=$PRIVATE_IP"
  echo "hostname=$(hostname)"
  echo "cpu_count=$CPU"
  echo "memory_kb=$MEM_KB"
  echo "free_disk_kb=$FREE_KB"
  echo "ntp_synchronized=$SYNC"
  echo "azure_vm_size=$VM_SIZE"
  echo '--- os-release'; cat /etc/os-release
  echo '--- uname'; uname -a
  echo '--- lscpu'; lscpu
  echo '--- memory'; free -h
  echo '--- routes'; ip route
  echo '--- azure metadata (sanitized)'; cat "$JSON"
} > "$TXT"
sha256sum "$TXT" "$JSON" > "$OUT/azure_${ROLE_LOW}_preflight.sha256"

echo "AZURE $ROLE_UP PREFLIGHT PASS"
echo "capture=$TXT"
echo "metadata=$JSON"
