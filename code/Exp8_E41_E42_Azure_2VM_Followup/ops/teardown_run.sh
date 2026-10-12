#!/usr/bin/env bash
set -euo pipefail
RUN_ID=${1:?}
k(){ sudo k3s kubectl "$@"; }
# Only after collection/analysis. Remove the run-specific webhook selector and test namespace.
k delete validatingwebhookconfiguration e39-action-revalidation --ignore-not-found
k delete namespace "e39-$RUN_ID" --ignore-not-found --wait=true
# The shared e39-system identities/webhook can remain for the next run; setup_run recreates its code/TLS/deployment state.
echo "teardown requested for e39-$RUN_ID"
