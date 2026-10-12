#!/bin/bash
# Delete ONLY the e38v2 kind cluster (dedicated kubeconfig), park its host logs under a label, recreate from
# deploy/kind-config.yaml, load the webhook image. Usage: recreate_cluster.sh LABEL
set -uo pipefail
LABEL=${1:?label}
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
kind get clusters | grep -qx e38v2 && kind delete cluster --name e38v2 --kubeconfig $KC
kind get clusters
mkdir -p $E/results/_parked_$LABEL
sudo mv $E/results/_audit_host $E/results/_parked_$LABEL/_audit_host 2>/dev/null || true
mv $E/results/_webhook_host $E/results/_parked_$LABEL/_webhook_host 2>/dev/null || true
rm -rf $E/.tokens_* $E/results/_render_*/certs
bash ~/e38v2r_ops/create_cluster.sh 2>&1 | tail -40
bash ~/e38v2r_ops/load_image.sh 2>&1 | tail -2
