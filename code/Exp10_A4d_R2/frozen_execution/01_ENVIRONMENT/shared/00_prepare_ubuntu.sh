#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo '/etc/os-release missing' >&2; exit 2
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  echo "This frozen Azure run expects Ubuntu 24.04 LTS; found ${ID:-unknown} ${VERSION_ID:-unknown}." >&2
  exit 2
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl openssl python3 python3-venv python3-pip tar gzip coreutils iproute2

echo 'Ubuntu prerequisite installation PASS.'
