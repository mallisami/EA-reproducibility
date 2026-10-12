#!/usr/bin/env bash
set -euo pipefail
[[ "${CONFIRM_RESET:-}" == YES ]] || { echo 'Destructive reset refused. Re-run with CONFIRM_RESET=YES only on the disposable experiment VM.' >&2; exit 2; }
if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then sudo /usr/local/bin/k3s-uninstall.sh; fi
sudo rm -rf /var/lib/rancher/k3s/server/logs /etc/rancher/k3s/audit-policy.yaml
sudo mkdir -p /var/lib/rancher/k3s/server/logs
echo 'Existing disposable K3s server removed.'
