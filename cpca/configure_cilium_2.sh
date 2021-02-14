#!/bin/bash

cd clustermesh-tools

# Apply the patch to all DaemonSets in all clusters
echo "Apply the patch to all DaemonSets in all clusters .........."
for cluster in "$@"
do
kubectl --context $cluster -n kube-system patch ds cilium -p "$(cat ds.patch)"
done

#Establish connections between clusters
echo "Establish connections between clusters............"
for cluster in "$@"
do
kubectl --context $cluster -n kube-system apply -f clustermesh.yaml
done

# Restart the cilium-agent in all clusters
echo "Restart the cilium-agent in all clusters ............"
for cluster in "$@"
do
kubectl --context $cluster -n kube-system delete pod -l k8s-app=cilium
done

# Restart the cilium-operator
echo "Restart the cilium-operator ......."
for cluster in "$@"
do
kubectl --context $cluster -n kube-system delete pod -l name=cilium-operator
done
