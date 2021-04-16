# mck8s: Container orchestrator for multi-cluster Kubernetes

mck8s, short for multi-cluster Kubernetes, allows you to automate the deployment of multi-cluster applications on multiple Kubernetes clusters by offering enhanced configuration possibilities. The main aim of mck8s is maximizing resource utilization and supporting elasitcity across multiple Kubenetes clusters by providing multiple placement policies, as well as bursting, cloud resource provisioning, autoscaling and de-provisioning capabilities. mck8s builds upon other open-source software such as [Kubernetes], [Kubernetes Federation], [kopf], [serf], [Cilium], [Cluster API], and [Prometheus]. 

# Architecture

The figure below shows the architecture of mck8s.

<p align="center"><img src="docs/images/mck8s_architecture.png" width="711"></p>

[Kubernetes]: https://github.com/kubernetes/kubernetes
[Kubernetes Federation]: https://github.com/kubernetes-sigs/kubefed
[kopf]: https://github.com/nolar/kopf
[serf]: https://github.com/hashicorp/serf
[Cilium]: https://github.com/cilium/cilium
[Cluster API]: https://github.com/kubernetes-sigs/cluster-api
[Prometheus]: https://github.com/prometheus/prometheus
