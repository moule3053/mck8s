from kubernetes import client, config
from collections import defaultdict
from pint        import UnitRegistry
import yaml
import time
import subprocess
import math

timeout = 60

ureg = UnitRegistry()
Q_ = ureg.Quantity
# Memory units
ureg.define('kmemunits = 1 = [kmemunits]')
ureg.define('Ki = 1024 * kmemunits')
ureg.define('Mi = Ki^2')
ureg.define('Gi = Ki^3')
ureg.define('Ti = Ki^4')
ureg.define('Pi = Ki^5')
ureg.define('Ei = Ki^6')

# cpu units
ureg.define('kcpuunits = 1 = [kcpuunits]')
ureg.define('m = 1/1000 * kcpuunits')
ureg.define('k = 1000 * kcpuunits')
ureg.define('M = k^2')
ureg.define('G = k^3')
ureg.define('T = k^4')
ureg.define('P = k^5')
ureg.define('E = k^6')

def get_all_federation_clusters():
    config.load_kube_config()

    api_instance = client.CustomObjectsApi()

    group = 'core.kubefed.io'  # str | The custom resource's group name
    version = 'v1beta1'  # str | The custom resource's version
    namespace = 'kube-federation-system'  # str | The custom resource's namespace
    plural = 'kubefedclusters'  # str | The custom resource's plural name. For TPRs this would be lowercase plural kind.
    pretty = 'true'

    clusters = []

    try:
        api_response = api_instance.list_namespaced_custom_object(group, version, namespace, plural, pretty=pretty, _request_timeout=timeout)
        for item in api_response['items']:
            clusters.append(item['metadata']['name'])
    except:
        print("Connection timeout after " + str(timeout) + " seconds to host cluster")

    return clusters

def getNodeIPs(cluster):
    config.load_kube_config()

    client_cluster = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    nodes = client_cluster.list_node()

    node_addresses = []

    for item in nodes.items:
        for add in item.status.addresses:
            if add.type == 'ExternalIP':
                node_addresses.append(add.address)

    return node_addresses

def getFlavors():
    # Return dict of available flavors
    # TO DO: To be supplied by users

    k8s_flavor_list = []
    k8s_flavor_list.append({'name': 'k8s.small', 'cpu': 2000, 'memory': 8192})
    k8s_flavor_list.append({'name': 'k8s.medium', 'cpu': 4000, 'memory': 16384})
    k8s_flavor_list.append({'name': 'k8s.large', 'cpu': 8000, 'memory': 32768})
    k8s_flavor_list.append({'name': 'k8s.xlarge', 'cpu': 16000, 'memory': 65536})

    return k8s_flavor_list

def getFlavor():
    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    plural_deploy = 'multiclusterdeployments'
    plural_job = 'multiclusterjobs'

    api_response_deploy = api.list_cluster_custom_object(plural=plural_deploy, group=group, version=version)
    api_response_job = api.list_cluster_custom_object(plural=plural_job, group=group, version=version)

    k8s_flavor_list = getFlavors()

    apps_list = []

    for item in api_response_deploy['items']:
        dict = {}
        if 'status' in item:
            if 'message' in item['status']:
                if item['status']['message']['message'] == 'to_cloud':

                    cloud_replicas = item['status']['message']['replicas']

                    dict['cpu_req'] = int(
                        item['spec']['template']['spec']['containers'][0]['resources']['requests']['cpu'][:-1])
                    dict['mem_req'] = int(
                        item['spec']['template']['spec']['containers'][0]['resources']['requests']['memory'][:-2])
                    dict['total_app_cpu'] = cloud_replicas * dict['cpu_req']
                    dict['total_app_memory'] = cloud_replicas * dict['mem_req']
                    apps_list.append(dict)

    for item in api_response_job['items']:
        dict = {}
        if 'status' in item:
            if 'message' in item['status']:
                if item['status']['message']['message'] == 'to_cloud':

                    cloud_replicas = item['status']['message']['replicas']

                    dict['cpu_req'] = int(
                        item['spec']['template']['spec']['containers'][0]['resources']['requests']['cpu'][:-1])
                    dict['mem_req'] = int(
                        item['spec']['template']['spec']['containers'][0]['resources']['requests']['memory'][:-2])
                    dict['total_app_cpu'] = cloud_replicas * dict['cpu_req']
                    dict['total_app_memory'] = cloud_replicas * dict['mem_req']
                    apps_list.append(dict)

    total_apps_cpu = 0
    total_apps_memory = 0

    for app in apps_list:
        total_apps_cpu += app['total_app_cpu']
        total_apps_memory += app['total_app_memory']

    # Select k8s flavor
    k8s_flavor = {}

    # For now consider only one node type
    # TO DO: multiple node types
    node_cpu = 8000
    node_memory = 32 * 1024

    for flavor in k8s_flavor_list:
        if flavor['cpu'] >= node_cpu and flavor['memory'] >= node_memory:
            k8s_flavor['name'] = flavor['name']
            k8s_flavor['cpu'] = flavor['cpu']
            k8s_flavor['memory'] = flavor['memory']
            break

    node_count = math.ceil(max(total_apps_cpu/k8s_flavor['cpu'], total_apps_memory/k8s_flavor['memory']))

    return k8s_flavor['name'], node_count

def getCloudCluster():
    all_clusters = get_all_federation_clusters()
    cloud_cluster = ""
    for cluster in all_clusters:
        if 'cloud' in cluster:
            return cluster
        else:
            return cloud_cluster

def provisionCloudCluster(cloud_cluster_name, k8s_flavor, node_count, master_ip, gateway_ip, ext_network_id, clouds_yaml, cert_text, influxdb_ip, security_group_id):
    cloud_cluster_name = cloud_cluster_name
    cloud_cluster_ns = cloud_cluster_name
    master_ip = master_ip
    gateway_ip = gateway_ip
    ext_network_id = ext_network_id
    node_cidr = "10.10.0.0/24"
    clouds_yaml = clouds_yaml
    k8s_flavor = k8s_flavor
    base_image = "ubuntu-k8s"
    node_replicas = node_count
    failure_domain = "nova"
    k8s_version = "v1.18.0"
    cert_text = cert_text
    security_group_id = security_group_id

    prom_remote_template = '''cat > prom-remote.yaml <<EOF
prometheus:
 prometheusSpec:
   remoteWrite:
   - url: "http://{influxdb_ip}:8086/api/v1/prom/write?db=cloud_prometheus"
   - url: "http://{influxdb_ip}:8428/api/v1/write"
EOF'''

    prom_remote_command = prom_remote_template.format(influxdb_ip=influxdb_ip)

    # Create the templates
    cluster_template = '''
    apiVersion: cluster.x-k8s.io/v1alpha3
    kind: Cluster
    metadata:
      name: {cluster_name}
      namespace: {namespace}
    spec:
      clusterNetwork:
        pods:
          cidrBlocks:
          - 106.96.0.0/11
        serviceDomain: cluster.local
      controlPlaneRef:
        apiVersion: controlplane.cluster.x-k8s.io/v1alpha3
        kind: KubeadmControlPlane
        name: {cluster_name}-control-plane
      infrastructureRef:
        apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
        kind: OpenStackCluster
        name: {cluster_name}
        '''

    cluster_yaml = cluster_template.format(cluster_name=cloud_cluster_name, namespace=cloud_cluster_ns)
    cluster_yaml = yaml.safe_load(cluster_yaml)

    openstackcluster_template = '''
    apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
    kind: OpenStackCluster
    metadata:
      name: {cluster_name}
      namespace: {namespace}
    spec:
      controlPlaneEndpoint:
        host: {master_ip}
        port: 6443
      cloudName: openstack
      cloudsSecret:
        name: {cluster_name}-cloud-config
        namespace: {namespace}
      disablePortSecurity: false
      disableServerTags: true
      dnsNameservers:
      - 8.8.8.8
      externalNetworkId: {ext_network_id}
      managedAPIServerLoadBalancer: false
      managedSecurityGroups: false
      useOctavia: false
      network:
        name: private
      subnet:
        name: private-subnet
      '''

    openstackcluster_yaml = openstackcluster_template.format(cluster_name=cloud_cluster_name,
                                                             namespace=cloud_cluster_ns,
                                                             master_ip=master_ip, ext_network_id=ext_network_id)
    openstackcluster_yaml = yaml.safe_load(openstackcluster_yaml)

    kubeadmcontrolplane_template = '''
    apiVersion: controlplane.cluster.x-k8s.io/v1alpha3
    kind: KubeadmControlPlane
    metadata:
      name: {cluster_name}-control-plane
      namespace: {namespace}
    spec:
      infrastructureTemplate:
        apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
        kind: OpenStackMachineTemplate
        name: {cluster_name}-control-plane
      kubeadmConfigSpec:
        clusterConfiguration:
          apiServer:
            extraArgs:
              cloud-config: /etc/kubernetes/cloud.conf
              cloud-provider: openstack
              feature-gates: "TTLAfterFinished=true"
            extraVolumes:
            - hostPath: /etc/kubernetes/cloud.conf
              mountPath: /etc/kubernetes/cloud.conf
              name: cloud
              readOnly: true
          controlPlaneEndpoint: {master_ip}:6443
          controllerManager:
            extraArgs:
              cloud-config: /etc/kubernetes/cloud.conf
              cloud-provider: openstack
              feature-gates: "TTLAfterFinished=true"
            extraVolumes:
            - hostPath: /etc/kubernetes/cloud.conf
              mountPath: /etc/kubernetes/cloud.conf
              name: cloud
              readOnly: true
            - hostPath: /etc/certs/cacert
              mountPath: /etc/certs/cacert
              name: cacerts
              readOnly: true
          imageRepository: k8s.gcr.io
        files:
        - content: {cert_text}
          encoding: base64
          owner: root
          path: /etc/kubernetes/cloud.conf
          permissions: "0600"
        - content: Cg==
          encoding: base64
          owner: root
          path: /etc/certs/cacert
          permissions: "0600"
        initConfiguration:
          nodeRegistration:
            kubeletExtraArgs:
              cloud-config: /etc/kubernetes/cloud.conf
              cloud-provider: openstack
            name: '{{{{ local_hostname }}}}'
        joinConfiguration:
          nodeRegistration:
            kubeletExtraArgs:
              cloud-config: /etc/kubernetes/cloud.conf
              cloud-provider: openstack
            name: '{{{{ local_hostname }}}}'
        ntp:
          servers: []
        users:
        - name: capo
          sshAuthorizedKeys:
          - ssh-rsa xxxxxxxxxxxxxxxxxxxxxxxxxx
          sudo: ALL=(ALL) NOPASSWD:ALL
      replicas: 1
      version: {k8s_version}
      '''

    kubeadmcontrolplane_yaml = kubeadmcontrolplane_template.format(cluster_name=cloud_cluster_name,
                                                                   namespace=cloud_cluster_ns,
                                                                   k8s_version=k8s_version, master_ip=master_ip, cert_text=cert_text)
    kubeadmcontrolplane_yaml = yaml.safe_load(kubeadmcontrolplane_yaml)

    openstackmachinetemplate_master_template = '''
    apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
    kind: OpenStackMachineTemplate
    metadata:
      name: {cluster_name}-control-plane
      namespace: {namespace}
    spec:
      template:
        spec:
          floatingIP: {master_ip}
          securityGroups:
            - uuid: {security_group_id}
          cloudName: openstack
          cloudsSecret:
            name: {cluster_name}-cloud-config
            namespace: {namespace}
          flavor: {k8s_flavor}
          image: {base_image}
          sshKeyName: cluster-api-provider-openstack
          networks:
          - filter:
              name: 'private'
            subnets:
            - filter:
                name: 'private-subnet'          
          '''
    openstackmachinetemplate_master_yaml = openstackmachinetemplate_master_template.format(
        cluster_name=cloud_cluster_name,
        namespace=cloud_cluster_ns, k8s_flavor=k8s_flavor, master_ip=master_ip, base_image=base_image,
        security_group_id=security_group_id)
    openstackmachinetemplate_master_yaml = yaml.safe_load(openstackmachinetemplate_master_yaml)

    machinedeployment_template = '''
    apiVersion: cluster.x-k8s.io/v1alpha3
    kind: MachineDeployment
    metadata:
      name: {cluster_name}-md-0
      namespace: {namespace}
    spec:
      clusterName: {cluster_name}
      replicas: {node_replicas}
      selector:
        matchLabels: null
      template:
        spec:
          bootstrap:
            configRef:
              apiVersion: bootstrap.cluster.x-k8s.io/v1alpha3
              kind: KubeadmConfigTemplate
              name: {cluster_name}-md-0
          clusterName: {cluster_name}
          failureDomain: {failure_domain}
          infrastructureRef:
            apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
            kind: OpenStackMachineTemplate
            name: {cluster_name}-md-0
          version: {k8s_version}
          '''
    machinedeployment_yaml = machinedeployment_template.format(cluster_name=cloud_cluster_name,
                                                               namespace=cloud_cluster_ns,
                                                               node_replicas=node_replicas,
                                                               failure_domain=failure_domain, k8s_version=k8s_version)
    machinedeployment_yaml = yaml.safe_load(machinedeployment_yaml)

    openstackmachinetemplate_worker_template = '''
    apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
    kind: OpenStackMachineTemplate
    metadata:
      name: {cluster_name}-md-0
      namespace: {namespace}
    spec:
      template:
        spec:
          securityGroups:
            - uuid: {security_group_id}
          cloudName: openstack
          cloudsSecret:
            name: {cluster_name}-cloud-config
            namespace: {namespace}
          flavor: {k8s_flavor}
          image: {base_image}
          sshKeyName: cluster-api-provider-openstack
          networks:
          - filter:
              name: 'public'
            subnets:
            - filter:
                name: 'public-subnet'          
          '''
    openstackmachinetemplate_worker_yaml = openstackmachinetemplate_worker_template.format(
        cluster_name=cloud_cluster_name, namespace=cloud_cluster_ns,
        k8s_flavor=k8s_flavor, base_image=base_image, security_group_id=security_group_id)
    openstackmachinetemplate_worker_yaml = yaml.safe_load(openstackmachinetemplate_worker_yaml)

    kubeadmconfigtemplate_template = '''
    apiVersion: bootstrap.cluster.x-k8s.io/v1alpha3
    kind: KubeadmConfigTemplate
    metadata:
      name: {cluster_name}-md-0
      namespace: {namespace}
    spec:
      template:
        spec:
          preKubeadmCommands: 
          - sudo ip route add 10.0.0.0/24 via {gateway_ip}
          files:
          - content: {cert_text}
            encoding: base64
            owner: root
            path: /etc/kubernetes/cloud.conf
            permissions: "0600"
          - content: Cg==
            encoding: base64
            owner: root
            path: /etc/certs/cacert
            permissions: "0600"
          joinConfiguration:
            nodeRegistration:
              kubeletExtraArgs:
                cloud-config: /etc/kubernetes/cloud.conf
                cloud-provider: openstack
              name: '{{{{ local_hostname }}}}'
          ntp:
            servers: []
          users:
          - name: capo
            sshAuthorizedKeys:
            - ssh-rsa xxxxxxxxxxxxxxxxxxxxxxxxxx
            sudo: ALL=(ALL) NOPASSWD:ALL
            '''
    kubeadmconfigtemplate_yaml = kubeadmconfigtemplate_template.format(cluster_name=cloud_cluster_name, gateway_ip=gateway_ip,
                                                                       namespace=cloud_cluster_ns, cert_text=cert_text)
    kubeadmconfigtemplate_yaml = yaml.safe_load(kubeadmconfigtemplate_yaml)

    secret_template = '''
    apiVersion: v1
    data:
      cacert: Cg==
      clouds.yaml: {clouds_yaml}
    kind: Secret
    metadata:
      name: {cluster_name}-cloud-config
      namespace: {namespace}
      '''
    secret_yaml = secret_template.format(clouds_yaml=clouds_yaml, cluster_name=cloud_cluster_name,
                                         namespace=cloud_cluster_ns)
    secret_yaml = yaml.safe_load(secret_yaml)

    namespace_template = '''
    apiVersion: v1
    kind: Namespace
    metadata:
      name: {namespace}
      '''
    namespace_yaml = namespace_template.format(namespace=cloud_cluster_ns)
    namespace_yaml = yaml.safe_load(namespace_yaml)

    # Load k8s contexts
    config.load_kube_config()

    # Core api
    api = client.CoreV1Api()

    # Create namespace
    try:
        api.create_namespace(
            body=namespace_yaml,
        )
        print("Namespace created")
    except:
        pass

    # Custom objects api
    api = client.CustomObjectsApi()
    #
    # Create cluster
    try:
        api.create_namespaced_custom_object(
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="clusters",
            body=cluster_yaml,
        )
        print("Cluster created")
    except:
        pass

    # Create OpenStackCluster
    try:
        api.create_namespaced_custom_object(
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackclusters",
            body=openstackcluster_yaml,
        )
        print("OpenStackCluster created")
    except:
        pass

    # Create KubeadmControlPlane
    try:
        api.create_namespaced_custom_object(
            group="controlplane.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="kubeadmcontrolplanes",
            body=kubeadmcontrolplane_yaml,
        )
        print("KubeadmControlPlane created")
    except:
        pass

    # Create OpenStackMachineTemplate for master node
    try:
        api.create_namespaced_custom_object(
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackmachinetemplates",
            body=openstackmachinetemplate_master_yaml,
        )
        print("OpenStackMachineTemplate for master node created")
    except:
        pass

    # Create MachineDeployment
    try:
        api.create_namespaced_custom_object(
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="machinedeployments",
            body=machinedeployment_yaml,
        )
        print("MachineDeployment created")
    except:
        pass
    # Create OpenStackMachineTemplate for worker nodes
    try:
        api.create_namespaced_custom_object(
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackmachinetemplates",
            body=openstackmachinetemplate_worker_yaml,
        )
        print("OpenStackMachineTemplate for worker nodes created")
    except:
        pass

    # Create KubeadmConfigTemplate
    try:
        api.create_namespaced_custom_object(
            group="bootstrap.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="kubeadmconfigtemplates",
            body=kubeadmconfigtemplate_yaml,
        )
        print("KubeadmConfigTemplate created")
    except:
        pass

    # Core api
    api = client.CoreV1Api()

    # Create Secret
    try:
        api.create_namespaced_secret(
            namespace=cloud_cluster_ns,
            body=secret_yaml,
        )
        print("Secret created")
    except:
        pass

    # Check if all machines are created

    # Custom objects api
    api = client.CustomObjectsApi()

    machines_count = 0

    while machines_count != node_replicas + 1:

        print("Waiting until cloud cluster comes live .....")

        machines = api.list_namespaced_custom_object(
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="machines",
        )
        machines_count = 0
        for item in machines['items']:
            if item['status']['phase'] == 'Running':
                machines_count += 1

        print("Waiting until all machines are ready ...... Sleep for 30 sec .......")
        time.sleep(30)

    # Delete previously created configs
    try:
        command = 'kubectl config delete-context ' + cloud_cluster_name
        result = subprocess.getoutput(command)
    except:
        pass

    try:
        command = 'kubectl config delete-cluster ' + cloud_cluster_name
        result = subprocess.getoutput(command)
    except:
        pass

    try:
        command = 'kubectl config unset users.'  + cloud_cluster_name + '-admin'
        result = subprocess.getoutput(command)
    except:
        pass

    # Get kubeconfig
    print("Getting KubeConfig of new cloud cluster ...")


    try:
        command = 'kubectl --namespace=' + cloud_cluster_ns + ' get secret ' + cloud_cluster_name + '-kubeconfig -o jsonpath={.data.value} | base64 --decode > ~/.kube/' + cloud_cluster_name
        result = subprocess.getoutput(command)
    except:
        pass

    # Deploy Cilium
    print("Deploying Cilium CNI ......")
    try:
        command = 'export clusterName=' + cloud_cluster_name + ' && envsubst < cilium.yaml | kubectl --kubeconfig ~/.kube/' + cloud_cluster_name + ' apply -f -'
        result = subprocess.getoutput(command)
    except:
        pass

    # Wait for 30 sec
    print("Waiting for 30 sec until Cilium is installed ..............")
    time.sleep(30)

    # Deploy metrics server
    print("Deploying metrics server ...")
    try:
        command = 'kubectl --kubeconfig ~/.kube/' + cloud_cluster_name + ' apply -f metrics_server.yaml'
        result = subprocess.getoutput(command)
    except:
        pass

    # Copy kube config
    print("Copying KubeConfig of new cloud cluster to the context ...")
    try:
        command = 'cp ~/.kube/config ~/.kube/config.backup'
        result = subprocess.getoutput(command)
    except:
        pass

    # Join kube configs
    print("Joining Kube Config contexts ...")
    try:
        command = 'KUBECONFIG=~/.kube/config.backup:~/.kube/' + cloud_cluster_name + ' kubectl config view --flatten > ~/.kube/config'
        result = subprocess.getoutput(command)
    except:
        pass

    # Rename context name
    print("Renaming context name of new cloud cluster ...")
    try:
        command = 'kubectl config rename-context ' + cloud_cluster_name + '-admin@' + cloud_cluster_name + ' ' + cloud_cluster_name
        result = subprocess.getoutput(command)
    except:
        pass
    # Join to federation
    print("Joining new cloud cluster to the federation ....")
    try:
        command = 'kubefedctl join ' + cloud_cluster_name + ' --cluster-context ' + cloud_cluster_name + ' --host-cluster-context cluster0 --v=2'
        result = subprocess.getoutput(command)
    except:
        pass

    # Delete ds.patch
    print("Deleting old files ...............")
    command = "cd clustermesh-tools/ && rm ds.patch clustermesh.yaml && rm -rf config"
    rc = subprocess.call(command, shell=True)

    # Configure Cilium 1
    print("Configuring Cilium 1...........")
    command = "./configure_cilium_1.sh cluster1 cluster2 cluster3 cluster4 cluster5 " + cloud_cluster_name
    rc = subprocess.call(command, shell=True)

    # Get IP addresses of nodes
    node_addresses = getNodeIPs(cloud_cluster_name)
    for add in node_addresses:
        f = open("clustermesh-tools/ds.patch", "a")
        f.write('      - ip: "' + add + '"\n')
        f.write('        hostnames:\n')
        f.write('        - ' + cloud_cluster_name + '.mesh.cilium.io\n')
        f.close()

    # Configure Cilium 2
    print("Configuring Cilium 2...........")
    command = "./configure_cilium_2.sh cluster1 cluster2 cluster3 cluster4 cluster5 " + cloud_cluster_name
    rc = subprocess.call(command, shell=True)

    # Prepare Prometheus remote template
    print("Prepare Prometheus remote template")
    try:
        subprocess.getoutput(prom_remote_command)
    except:
        pass

    # Install Prometheus Operator
    print("Install Prometheus operator ....")
    try:
        command1 = 'helm repo add prometheus-community https://prometheus-community.github.io/helm-charts'
        command2 = 'helm repo update'
        command3 = 'kubectl config use-context ' + cloud_cluster_name
        command4 = 'kubectl create ns monitoring'
        command5 = 'helm install prometheus-community/kube-prometheus-stack --generate-name --set grafana.service.type=NodePort --set prometheus.service.type=NodePort --set prometheus.prometheusSpec.scrapeInterval="5s" -f prom-remote.yaml --namespace monitoring'
        command6 = 'kubectl config use-context cluster0'
        result1 = subprocess.getoutput(command1)
        result2 = subprocess.getoutput(command2)
        result3 = subprocess.getoutput(command3)
        result4 = subprocess.getoutput(command4)
        result5 = subprocess.getoutput(command5)
        result6 = subprocess.getoutput(command6)
    except:
        pass

    return cloud_cluster_name

def cloudClusterInfo(cluster):
    config.load_kube_config()

    pending_pods_count = 0
    cpu_req = []
    memory_req = []
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    max_pods = 110
    field_selector = ("status.phase==Pending,")

    #pods = core_v1.list_pod_for_all_namespaces(limit=max_pods, field_selector=field_selector).items
    pods = core_v1.list_namespaced_pod(namespace='default', limit=max_pods, field_selector=field_selector).items

    for pod in pods:
        pending_pods_count += 1
        for container in pod.spec.containers:
            res = container.resources
            reqs = defaultdict(lambda: 0, res.requests or {})
            cpu_req.append(Q_(reqs["cpu"]))
            memory_req.append(Q_(reqs["memory"]))

    total_cpu = int(sum(cpu_req)) * 1000
    total_memory = int(sum(memory_req))

    return pending_pods_count, total_cpu, total_memory

def cloudNodesResources(cluster):
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    resources_per_node = []

    try:
        for node in core_v1.list_node(_request_timeout=timeout).items[1:]:
            stats          = {}
            node_name      = node.metadata.name
            allocatable    = node.status.allocatable
            allocatabale_cpu = Q_(allocatable['cpu']).to('m')
            allocatable_memory = Q_(allocatable['memory'])
            max_pods       = int(int(allocatable["pods"]) * 1.5)
            field_selector = ("status.phase!=Succeeded,status.phase!=Failed," +
                              "spec.nodeName=" + node_name)

            node_cpu_request = 0
            node_memory_request = 0

            pods = core_v1.list_pod_for_all_namespaces(limit=max_pods,
                                                       field_selector=field_selector).items
            cpureqs, memreqs = [], []
            for pod in pods:
                for container in pod.spec.containers:
                    res = container.resources
                    reqs = defaultdict(lambda: 0, res.requests or {})
                    cpureqs.append(Q_(reqs["cpu"]))
                    memreqs.append(Q_(reqs["memory"]))

            node_cpu_request += sum(cpureqs)
            node_memory_request += sum(memreqs)

            dict = {}

            dict['name'] = node_name
            dict['allocatable_cpu'] = float(allocatabale_cpu) * 1000
            dict['allocatable_memory'] = float(allocatable_memory) / (1024 * 1024)
            dict['available_cpu'] = float(allocatabale_cpu - node_cpu_request) * 1000
            dict['available_memory'] = float(allocatable_memory - node_memory_request) / (1024 * 1024)
            dict['total_cpu_request'] = float(node_cpu_request) * 1000
            dict['total_memory_request'] = float(node_memory_request) / (1024 * 1024)

            resources_per_node.append(dict)
    except:
        print("Connection timeout after " + str(timeout) + " seconds on cluster " + cluster)
    return resources_per_node

def scaleOut(cloud_cluster, pending_pods_count, total_cpu, total_memory):
    print("Scaling out the cloud cluster ...............")
    config.load_kube_config()

    api = client.CustomObjectsApi()

    k8s_flavor_list = getFlavors()

    # TO DO: Consider multiple machine deployments
    machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas = getMachineDeployment()

    #k8s_flavor = getK8sFlavor(machinedeployment_namespace)
    # For now only one node type
    # TO DO: consider multiple node types
    k8s_flavor = 'k8s.large'


    for flavor in k8s_flavor_list:
        if k8s_flavor == flavor['name']:
            node_cpu = flavor['cpu']
            node_memory = flavor['memory']

    #node_count = math.ceil(min((node_cpu/total_cpu), (node_memory/total_memory)))
    node_count = math.ceil(max(total_cpu/node_cpu, total_memory/node_memory))
    desired_node_replicas = node_count + machinedeployment_replicas

    print("Number of pending pods ..............", pending_pods_count)

    print("total cpu and total memory of pending pods", total_cpu, total_memory)

    print("Number of node replicas", desired_node_replicas)

    patchMachineDeployment(cloud_cluster, machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas, desired_node_replicas)

def patchMachineDeployment(cloud_cluster, machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas, desired_node_replicas):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    failure_domain = "nova"
    k8s_version = 'v1.18.0'

    machinedeployment_template = '''
    apiVersion: cluster.x-k8s.io/v1alpha3
    kind: MachineDeployment
    metadata:
      name: {cluster_name}-md-0
      namespace: {namespace}
    spec:
      clusterName: {cluster_name}
      replicas: {node_replicas}
      selector:
        matchLabels: null
      template:
        spec:
          bootstrap:
            configRef:
              apiVersion: bootstrap.cluster.x-k8s.io/v1alpha3
              kind: KubeadmConfigTemplate
              name: {cluster_name}-md-0
          clusterName: {cluster_name}
          failureDomain: {failure_domain}
          infrastructureRef:
            apiVersion: infrastructure.cluster.x-k8s.io/v1alpha3
            kind: OpenStackMachineTemplate
            name: {cluster_name}-md-0
          version: {k8s_version}
          '''
    machinedeployment_yaml = machinedeployment_template.format(cluster_name=cloud_cluster,
                                                               namespace=machinedeployment_namespace,
                                                               node_replicas=desired_node_replicas,
                                                               failure_domain=failure_domain, k8s_version=k8s_version)
    machinedeployment_yaml = yaml.safe_load(machinedeployment_yaml)


    # Patch MachineDeployment
    try:
        api.patch_namespaced_custom_object(
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=machinedeployment_namespace,
            plural="machinedeployments",
            body=machinedeployment_yaml,
            name=machinedeployment_name,
        )
        print("MachineDeployment patched")
    except:
       pass

    # TO DO: Check if the additional nodes are provisioned before continuing
    # instead of waiting for a fixed amount of time

    machines_count = 0

    while machines_count != desired_node_replicas + 1:

        print("Waiting until added nodes become alive .....")

        machines = api.list_namespaced_custom_object(
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=machinedeployment_namespace,
            plural="machines",
        )
        machines_count = 0
        for item in machines['items']:
            if item['status']['phase'] == 'Running':
                machines_count += 1

        print("Waiting until all machines are ready ...... Sleep for 30 sec .......")
        time.sleep(30)

def getMachineDeployment():
    config.load_kube_config()

    api = client.CustomObjectsApi()

    # TO DO: consider multiple machinedeployments

    machinedeployment = api.list_cluster_custom_object(
        group="cluster.x-k8s.io",
        version="v1alpha3",
        plural="machinedeployments",
    )

    machinedeployment_name = []
    machinedeployment_namespace = []
    machinedeployment_replicas = []

    if len(machinedeployment['items']) > 0:

        for item in machinedeployment['items']:
            machinedeployment_name.append(item['metadata']['name'])
            machinedeployment_namespace.append(item['metadata']['namespace'])
            machinedeployment_replicas.append(item['spec']['replicas'])

        return machinedeployment_name[0], machinedeployment_namespace[0], machinedeployment_replicas[0]
    else:
        return "", "", ""

def getK8sFlavor(namespace):
    config.load_kube_config()

    api = client.CustomObjectsApi()

    openstackmachinetemplate = api.list_namespaced_custom_object(
        group="infrastructure.cluster.x-k8s.io",
        version="v1alpha3",
        namespace=namespace,
        plural="openstackmachinetemplates",
    )

    k8s_flavor = []

    for item in openstackmachinetemplate['items']:
        k8s_flavor.append(item['spec']['template']['spec']['flavor'])

    return k8s_flavor[0]

def getCloudApps():
    config.load_kube_config()

    cluster = getCloudCluster()
    namespace = 'default'
    cloud_apps = []

    api = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    pods = api.list_namespaced_pod(namespace=namespace)

    for item in pods.items:
        for container in item.spec.containers:
            cloud_apps.append(container.name)

    return cloud_apps



def deprovisionCloudCluster(cloud_cluster_name, cloud_cluster_ns):

    # Remove cluster from federation cluster registry
    print("Remove cluster from federation cluster registry ....")
    try:
        command = 'kubefedctl unjoin ' + cloud_cluster_name + ' --cluster-context ' + cloud_cluster_name + ' --host-cluster-context cluster0 --v=2'
        result = subprocess.getoutput(command)
    except:
        pass

    config.load_kube_config()

    api = client.CustomObjectsApi()

    # delete MachineDeployment
    try:
        machinedeployment_name = cloud_cluster_name + "-md-0"
        api.delete_namespaced_custom_object(
            name=machinedeployment_name,
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="machinedeployments",
        )
        print("MachineDeployment deleted")
    except:
        pass

    # delete OpenStackMachineTemplate for worker nodes
    try:
        openstackmachinetemplate_worker_name = cloud_cluster_name + "-md-0"
        api.delete_namespaced_custom_object(
            name=openstackmachinetemplate_worker_name,
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackmachinetemplates",
        )
        print("OpenStackMachineTemplate for worker nodes deleted")
    except:
        pass

    machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas = getMachineDeployment()

    while machinedeployment_replicas != "":
        # Sleep for check until machinedeployment deleted
        print("Sleep for 15 sec until machinedeployment deleted")
        time.sleep(15)
        machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas = getMachineDeployment()

    # delete KubeadmControlPlane
    try:
        kubeadmcontrolplane_name = cloud_cluster_name + "-control-plane"
        api.delete_namespaced_custom_object(
            name=kubeadmcontrolplane_name,
            group="controlplane.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="kubeadmcontrolplanes",
        )
        print("KubeadmControlPlane deleted")
    except:
        pass

    # delete OpenStackMachineTemplate for master node
    try:
        openstackmachinetemplate_master_name = cloud_cluster_name + "-control-plane"
        api.delete_namespaced_custom_object(
            name=openstackmachinetemplate_master_name,
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackmachinetemplates",
        )
        print("OpenStackMachineTemplate for master node deleted")
    except:
        pass

    # delete KubeadmConfigTemplate
    try:
        kubeadmconfigtemplate_name = cloud_cluster_name + "-md-0"
        api.delete_namespaced_custom_object(
            name=kubeadmconfigtemplate_name,
            group="bootstrap.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="kubeadmconfigtemplates",
        )
        print("KubeadmConfigTemplate deleteed")
    except:
        pass

    # Sleep for check until control plane deleted
    print("Sleep for 30 sec until control plane deleted")
    time.sleep(30)

    # delete OpenStackCluster
    try:
        api.delete_namespaced_custom_object(
            name=cloud_cluster_name,
            group="infrastructure.cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="openstackclusters",
            # body=openstackcluster_yaml,
        )
        print("OpenStackCluster deleted")
    except:
        pass

    # Sleep for check until OpenStackCluster deleted
    print("Sleep for 15 sec until OpenStackCluster deleted")
    time.sleep(15)

    try:
        # delete cluster
        api.delete_namespaced_custom_object(
            name=cloud_cluster_name,
            group="cluster.x-k8s.io",
            version="v1alpha3",
            namespace=cloud_cluster_ns,
            plural="clusters",
        )
        print("Cluster deleted")
    except:
        pass

        # Sleep for check until Cluster deleted
        print("Sleep for 15 sec until Cluster deleted")
        time.sleep(15)

    try:
        # Core api
        api = client.CoreV1Api()

        # delete Secret
        secret_name = cloud_cluster_name + "-cloud-config"
        api.delete_namespaced_secret(
            name=secret_name,
            namespace=cloud_cluster_ns,
        )
        print("Secret deleted")
    except:
        pass

    # Core api
    try:
        api = client.CoreV1Api()

        # delete namespace
        api.delete_namespace(
            name=cloud_cluster_name,
        )
        print("Namespace deleted")
    except:
        pass

    # Sleep for 15 sec until namespace deleted
    print("Sleep for 15 sec until namespace deleted")
    time.sleep(60)
