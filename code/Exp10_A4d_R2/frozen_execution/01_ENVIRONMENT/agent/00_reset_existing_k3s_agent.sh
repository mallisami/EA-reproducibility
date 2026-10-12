#!/usr/bin/env bash
set -euo pipefail
[[ "${CONFIRM_RESET:-}" == YES ]] || { echo 'Destructive reset refused. Re-run with CONFIRM_RESET=YES only on the disposable experiment VM.' >&2; exit 2; }
if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then sudo /usr/local/bin/k3s-agent-uninstall.sh; fi
echo 'Existing disposable K3s agent removed.'
