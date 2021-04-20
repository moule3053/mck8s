# mck8s: Container orchestrator for multi-cluster Kubernetes

mck8s, short for multi-cluster Kubernetes, allows you to automate the deployment of multi-cluster applications on multiple Kubernetes clusters by offering enhanced configuration possibilities. The main aim of mck8s is maximizing resource utilization and supporting elasitcity across multiple Kubenetes clusters by providing multiple placement policies, as well as bursting, cloud resource provisioning, autoscaling and de-provisioning capabilities. mck8s builds upon other open-source software such as [Kubernetes], [Kubernetes Federation], [kopf], [serf], [Cilium], [Cluster API], and [Prometheus]. 

# Architecture

The figure below shows the architecture of mck8s.

<p align="center"><img src="docs/images/mck8s_architecture.png" width="711"></p>

# Quick start

## Pre-requisites

- A Kubernetes cluster to act as the mck8s management cluster. Since mck8s components need access to the Kubernetes control plane, managed Kubernetes offerings such as GKE are not supported at the moment.
- A number of Kubernetes clusters to be managed by the management cluster and on which workloads run. We assume that you have administrative access to all these clusters and the Kubernetes `kubeconfig` files of all clusters are available.
- If traffic routing between clusters is desired, it is recommended to deploy [Cilium Cluster Mesh] on the workload clusters with distinct Pod CIDRs.
- If proximity-aware placement is desired, [serf] should be deployed on at least one node of each workload cluster.
- If cloud provisioning and autoscaling ia desired, cloud credentials are required. For now, we support OpenStack clusters.

## More to come ...

## Workload clusters

[Kubernetes]: https://github.com/kubernetes/kubernetes
[Kubernetes Federation]: https://github.com/kubernetes-sigs/kubefed
[kopf]: https://github.com/nolar/kopf
[serf]: https://github.com/hashicorp/serf
[Cilium]: https://github.com/cilium/cilium
[Cluster API]: https://github.com/kubernetes-sigs/cluster-api
[Prometheus]: https://github.com/prometheus/prometheus
[Cilium Cluster Mesh]: https://docs.cilium.io/en/stable/gettingstarted/clustermesh/#deploying-a-simple-example-service
