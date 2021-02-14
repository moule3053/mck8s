#!/bin/bash

set -e

DIR="${OUTPUT:-config}"
KUBECONFIG="${KUBECONFIG:+--kubeconfig=${KUBECONFIG}}"

kubectl="kubectl $KUBECONFIG"

for SECRET in "$DIR"/*; do
	if [[ ! "$SECRET" =~ .ips$ ]]; then
		echo "--from-file=$SECRET"
	fi
done | xargs kubectl create --dry-run=true secret generic cilium-clustermesh -o yaml
