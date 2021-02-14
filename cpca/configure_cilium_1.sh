#!/bin/bash

# Expose Cilium etcd to other clusters
echo "Expose Cilium etcd to other clusters .........."
for cluster in "$@"
do
kubectl --context $cluster -n kube-system apply -f cilium-etcd-external-nodeport.yaml
done

# Install jq
#echo "Installing jq ..........."
#apt -y install jq

# Extract the TLS keys and generate the etcd configuration
echo "Extract the TLS keys and generate the etcd configuration ............"
#cd ~/ && git clone https://github.com/cilium/clustermesh-tools.git
cd clustermesh-tools

for cluster in "$@"
do
kubectl config use-context $cluster
./extract-etcd-secrets.sh
kubectl config use-context cluster0
done

# Generate a single Kubernetes secret from all the keys and certificates extracted
echo "Generate a single Kubernetes secret from all the keys and certificates extracted ........"
./generate-secret-yaml.sh > clustermesh.yaml

# Ensure that the etcd service names can be resolved
#echo "Ensure that the etcd service names can be resolved........."
./generate-name-mapping.sh > ds.patch
