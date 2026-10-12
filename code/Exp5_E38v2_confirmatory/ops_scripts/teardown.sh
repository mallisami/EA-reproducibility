#!/bin/bash
# Delete ONLY the e38v2 kind cluster after a verified export. Usage: teardown.sh RUN_ID
set -uo pipefail
RUN_ID=$1
E=~/e38v2r_20260926
KC=$E/.kubeconfig_e38v2
test -f $E/results/e38v2r_EXPORT_$RUN_ID.tar.gz.sha256 || { echo "no export; abort"; exit 1; }
kind get clusters | tee /tmp/e38v2r_clusters_pre_delete.txt
docker ps --format '{{.Names}}' | grep -v '^e38v2-' | sort > /tmp/e38v2r_docker_others_before_delete.txt
kind delete cluster --name e38v2 --kubeconfig $KC
kind get clusters | tee /tmp/e38v2r_clusters_after.txt
diff <(grep -v '^e38v2$' /tmp/e38v2r_clusters_before.txt) /tmp/e38v2r_clusters_after.txt && echo "other clusters untouched (vs preflight list)"
docker ps --format '{{.Names}}' | sort > /tmp/e38v2r_docker_after.txt
diff /tmp/e38v2r_docker_others_before_delete.txt /tmp/e38v2r_docker_after.txt && echo "other containers unchanged"
kubectl config current-context
rm -rf $E/.tokens_* $E/results/_render_*/certs $KC
ls -d $E/.tokens_* 2>/dev/null || echo "tokens removed"
free -m | sed -n 2p
date -u +%FT%TZ
