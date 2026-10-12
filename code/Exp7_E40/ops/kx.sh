#!/usr/bin/env bash
# exec a shell command inside the E40 runner pod
W=$HOME/e40_20260926
P=$(kubectl --kubeconfig $W/kubeconfig -n e40-system get pod -l app=e40-runner -o jsonpath='{.items[0].metadata.name}')
exec nice kubectl --kubeconfig $W/kubeconfig -n e40-system exec "$P" -c runner -- sh -c "$*"
