#!/bin/bash 

KUBECONFIG=~/.kube/cluster0:~/.kube/cluster1:~/.kube/cluster2:~/.kube/cluster3:~/.kube/cluster4:~/.kube/cluster5 kubectl config view --flatten > ~/.kube/config

for i in {0..5}
do
kubectl config rename-context k8s-admin-cluster$i@kubernetes cluster$i
done

# Install helm3
wget --tries=0 https://get.helm.sh/helm-v3.3.1-linux-amd64.tar.gz
tar xzvf helm-v3.3.1-linux-amd64.tar.gz
mv linux-amd64/helm /usr/local/bin/
helm repo add stable https://charts.helm.sh/stable
helm repo add stable https://kubernetes-charts.storage.googleapis.com/
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Deploy Prometheus on member clusters
for i in {1..5}
do
kubectl config use-context cluster$i; kubectl create ns monitoring; helm install prometheus-community/kube-prometheus-stack --generate-name --set grafana.service.type=NodePort --set prometheus.service.type=NodePort --set prometheus.prometheusSpec.scrapeInterval="5s" --namespace monitoring; kubectl config use-context cluster0
done

# Deploy Prometheus Federation on Cluster 0
kubectl create ns monitoring
helm install prometheus-community/kube-prometheus-stack --generate-name --set grafana.service.type=NodePort --set prometheus.service.type=NodePort --set prometheus.prometheusSpec.scrapeInterval="5s" --namespace monitoring --values values.yaml

# Install kubefedctl
wget --tries=0 https://github.com/kubernetes-sigs/kubefed/releases/download/v0.1.0-rc6/kubefedctl-0.1.0-rc6-linux-amd64.tgz
tar xzvf kubefedctl-0.1.0-rc6-linux-amd64.tgz
mv kubefedctl /usr/local/bin/

# Add helm chart
sleep 30
kubectl config use-context cluster0
helm repo add kubefed-charts https://raw.githubusercontent.com/kubernetes-sigs/kubefed/master/charts
helm repo update

# Deploy KubeFed
helm --namespace kube-federation-system upgrade -i kubefed kubefed-charts/kubefed --version=0.4.0 --create-namespace


# Join clusters to KubeFed
sleep 30
for i in {1..5}
do
kubefedctl join cluster$i --cluster-context cluster$i --host-cluster-context cluster0 --v=2
done

# Download and Install golang
wget https://dl.google.com/go/go1.13.8.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.13.8.linux-amd64.tar.gz
echo "export PATH=$PATH:/usr/local/go/bin" >> $HOME/.profile
source $HOME/.profile

# Deploy metrics server
wget https://gist.githubusercontent.com/moule3053/1b14b7898fd473b4196bdccab6cc7b48/raw/916f4362bcde612d0f96af48bc7ef7b99ab06a1f/metrics_server.yaml
for i in {0..5}
do
	kubectl --context=cluster$i create -f metrics_server.yaml
done

# Download and install clusterctl
curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/v0.3.10/clusterctl-linux-amd64 -o clusterctl
chmod +x ./clusterctl
sudo mv ./clusterctl /usr/local/bin/clusterctl

# Initialize clusterctl with OpenStack provider
clusterctl init --infrastructure openstack:v0.3.1

# Expose Cilium etcd to other clusters
echo "Expose Cilium etcd to other clusters .........."
for i in {1..5}
do
kubectl --context cluster$i -n kube-system apply -f https://raw.githubusercontent.com/cilium/cilium/v1.9/examples/kubernetes/clustermesh/cilium-etcd-external-service/cilium-etcd-external-nodeport.yaml
done

# Install jq
echo "Installing jq ..........."
apt -y install jq

# Extract the TLS keys and generate the etcd configuration
echo "Extract the TLS keys and generate the etcd configuration ............"
cd ~/ && git clone https://github.com/cilium/clustermesh-tools.git
cd clustermesh-tools

for i in {1..5}
do
kubectl config use-context cluster$i
./extract-etcd-secrets.sh
kubectl config use-context cluster0
done

# Generate a single Kubernetes secret from all the keys and certificates extracted
echo "Generate a single Kubernetes secret from all the keys and certificates extracted ........"
./generate-secret-yaml.sh > clustermesh.yaml

# Ensure that the etcd service names can be resolved
echo "Ensure that the etcd service names can be resolved........."
./generate-name-mapping.sh > ds.patch

# Apply the patch to all DaemonSets in all clusters
echo "Apply the patch to all DaemonSets in all clusters .........."
for i in {1..5}
do
kubectl --context cluster$i -n kube-system patch ds cilium -p "$(cat ds.patch)"
done

#Establish connections between clusters
echo "Establish connections between clusters............"
for i in {1..5}
do
kubectl --context cluster$i -n kube-system apply -f clustermesh.yaml
done

# Restart the cilium-agent in all clusters
echo "Restart the cilium-agent in all clusters ............"
for i in {1..5}
do
kubectl --context cluster$i -n kube-system delete pod -l k8s-app=cilium
done

# Restart the cilium-operator
echo "Restart the cilium-operator ......."
for i in {1..5}
do
kubectl --context cluster$i -n kube-system delete pod -l name=cilium-operator
done

echo "Done setting up Cilium cluster mesh!"

echo "DONE. Kubernetes Federation is setup."
