from kubernetes import client, config
from kubernetes.client.rest import ApiException
from collections import defaultdict
from pint        import UnitRegistry
from prometheus_api_client import PrometheusConnect
import subprocess
import operator
import os
import math
import pandas as pd

# Load k8s contexts
config.load_kube_config()

timeout = 30

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

def findPossibleReplacementClusters(cluster, original_clusters, app_cpu_request, app_memory_request):
    nearest_clusters = findNearestClusters(cluster, original_clusters)
    possible_clusters = []
    for c in nearest_clusters:
        check_possibility = checkClusterPossibility(c, app_cpu_request, app_memory_request)
        if check_possibility == True:
            possible_clusters.append(c)

    return possible_clusters

def findEligibleReplacementClusters(cluster, original_clusters, app_cpu_request, app_memory_request, replicas):
    print("Looking for clusters near to " + cluster)
    nearest_clusters = findNearestClusters(cluster, original_clusters)
    print("Nearest clusters ....", nearest_clusters)
    eligible_clusters = []
    for c in nearest_clusters:
        check_eligibility = checkClusterEligibility(c, app_cpu_request, app_memory_request, replicas)
        if check_eligibility == True:
            eligible_clusters.append(c)

    return eligible_clusters


def findPossibleClusters(clusters, app_cpu_request, app_memory_request):
    print("==================== Processing possible clusters ==========================")
    print(clusters)
    possible_clusters_list = []
    for cluster in clusters:
        is_possible = checkClusterPossibility(cluster, app_cpu_request, app_memory_request)

        if is_possible == True:
            possible_clusters_list.append(cluster)
        else:
            possible_replacements = findPossibleReplacementClusters(cluster, clusters, app_cpu_request, app_memory_request)

            print("Replacement clusters ...............", possible_replacements)

            if len(possible_replacements) > 0:
                possible_clusters_list.append(possible_replacements[0])

    return possible_clusters_list

def findEligibleClusters(fogapp_locations, possible_clusters, override_replicas_new, fogapp_cpu_request, fogapp_memory_request):
    eligible_clusters = []
    for cluster in possible_clusters:
        replicas = int(override_replicas_new[cluster])
        # is_eligible = checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas)
        # The maximum number of replicas the cluster can host
        maximum_replicas = getMaximumReplicas(cluster, fogapp_cpu_request, fogapp_memory_request)
        if maximum_replicas > replicas:
            dict = {}
            dict['name'] = cluster
            dict['max_replicas'] = maximum_replicas
            dict['replicas'] = replicas
            dict['overflow'] = 0
            eligible_clusters.append(dict)
        else:
            dict = {}
            dict['name'] = cluster
            dict['max_replicas'] = maximum_replicas
            dict['replicas'] = maximum_replicas
            dict['overflow'] = replicas - maximum_replicas
            eligible_clusters.append(dict)

    temp_list = []
    for cluster in eligible_clusters:
        temp_list.append(cluster)
    print("Possible list of clusters and oveflow ....", temp_list)

    temp_list_2 = []
    for cluster in temp_list:
        temp_list_2.append(cluster['name'])

    temp_list_3 = list(set(fogapp_locations + temp_list_2))

    total_overflow = 0

    for cluster in temp_list:
        total_overflow += cluster['overflow']

    maximum_replicas = {}

    for cluster in temp_list:
        nearest_clusters = []
        overflow = cluster['overflow']
        # leftover = overflow
        print("Overflow from ", cluster, overflow)

        if overflow > 0:
            nearest_clusters = findNearestClusters(cluster, temp_list_3)
            print("List of nearest clusters ....", nearest_clusters)
        # else:
        #     print("The cluster doesn't have overflow ....")
        #     break

        # Distribute overflow to nearest clusters
        if len(nearest_clusters) > 0:
            for c in nearest_clusters:
                # print("Overflow .................", overflow)
                # if overflow > 0:
                maximum_replicas[c] = getMaximumReplicas(c, fogapp_cpu_request, fogapp_memory_request)
                print("Maximum replicas .....", maximum_replicas)

    for cluster in temp_list:
        nearest_clusters = []
        overflow = cluster['overflow']
        if overflow > 0:
            nearest_clusters = findNearestClusters(cluster, temp_list_3)
        # else:
        #     break
        if len(nearest_clusters) > 0:
            for c in nearest_clusters:
                if cluster['overflow'] > 0:
                    if maximum_replicas[c] == 0:
                        cluster['overflow'] = cluster['overflow']
                        # break
                    elif maximum_replicas[c] > cluster['overflow']:
                        dict = {}
                        dict['name'] = c
                        dict['replicas'] = cluster['overflow']
                        dict['overflow'] = 0
                        eligible_clusters.append(dict)
                        maximum_replicas[c] = maximum_replicas[c] - cluster['overflow']
                        cluster['overflow'] = 0
                        # break
                    else:
                        dict = {}
                        dict['name'] = c
                        dict['replicas'] = maximum_replicas[c]
                        dict['overflow'] = 0
                        cluster['overflow'] = cluster['overflow'] - maximum_replicas[c]
                        eligible_clusters.append(dict)
                        maximum_replicas[c] = 0

    eligible_clusters = (pd.DataFrame(eligible_clusters)
                         .groupby(['name'], as_index=False)
                         .agg({'replicas': 'sum', 'overflow': 'sum'})
                         .to_dict('r'))

    return eligible_clusters

def checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas):
    print("==================== Processing eligible clusters ==========================")
    totalAvailableCPU, totalAvailableMemory, available_resources_per_node = compute_available_resources(cluster)

    print("Available resources per node ..... for cluster", cluster, available_resources_per_node)

    count = 0

    for node in available_resources_per_node:
        count += min(math.floor(node['cpu']/app_cpu_request), math.floor(node['memory']/app_memory_request))

    print("Total number of replicas that can be scheduled ........... on", cluster, count)

    #if count > replicas:
    if count > replicas:
        return True
    else:
        return False

def getAllocatableCapacity(cluster, app_cpu_request, app_memory_request, app_name, app_namespace):
    print("Compute allocatable capacity ..............")
    allocatable_capacity_per_node = computeAllocatableCapacity(cluster, app_name, app_namespace)

    count = 0

    for node in allocatable_capacity_per_node:
        count += min(math.floor(node['cpu']/app_cpu_request), math.floor(node['memory']/app_memory_request))

    return count


def getMaximumReplicas(cluster, app_cpu_request, app_memory_request):
    print("Get the maximum number of replicas > 0 clusters can run ....")
    totalAvailableCPU, totalAvailableMemory, available_resources_per_node = compute_available_resources(cluster)

    count = 0

    for node in available_resources_per_node:
        count += min(math.floor(node['cpu']/app_cpu_request), math.floor(node['memory']/app_memory_request))

    return count

def computeAllocatableCapacity(cluster, app_name, namespace):
    total_allocatable_cpu = 0
    total_allocatable_memory = 0

    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    allocatable_resources_per_node = []

    try:
        for node in core_v1.list_node(_request_timeout=timeout).items[1:]:
            stats = {}
            node_name = node.metadata.name
            allocatable = node.status.allocatable
            allocatabale_cpu = Q_(allocatable['cpu']).to('m')
            allocatable_memory = Q_(allocatable['memory'])
            total_allocatable_cpu += allocatabale_cpu
            total_allocatable_memory += allocatable_memory
            max_pods = int(int(allocatable["pods"]) * 1.5)
            field_selector = ("status.phase!=Succeeded,status.phase!=Failed," +
                              "spec.nodeName=" + node_name)

            # Calculate for all ns
            node_cpu_request_all = 0
            node_memory_request_all = 0

            pods = core_v1.list_pod_for_all_namespaces(limit=max_pods,
                                                       field_selector=field_selector).items
            cpureqs, memreqs = [], []
            for pod in pods:
                for container in pod.spec.containers:
                    res = container.resources
                    reqs = defaultdict(lambda: 0, res.requests or {})
                    cpureqs.append(Q_(reqs["cpu"]))
                    memreqs.append(Q_(reqs["memory"]))

            node_cpu_request_all += sum(cpureqs)
            node_memory_request_all += sum(memreqs)

            # Calculate for the namespace
            node_cpu_request_default = 0
            node_memory_request_default = 0

            # Get pods in the namespace
            pods = core_v1.list_namespaced_pod(namespace=namespace, limit=max_pods,
                                               field_selector=field_selector).items
            cpureqs, memreqs = [], []
            for pod in pods:
                for container in pod.spec.containers:
                    res = container.resources
                    reqs = defaultdict(lambda: 0, res.requests or {})
                    cpureqs.append(Q_(reqs["cpu"]))
                    memreqs.append(Q_(reqs["memory"]))

            node_cpu_request_default += sum(cpureqs)
            node_memory_request_default += sum(memreqs)

            # Exclude the resource request of other apps in the default namespace
            # Calculate for the namespace other apps
            node_cpu_request_default_other = 0
            node_memory_request_default_other = 0

            # Get pods of default ns
            pods = core_v1.list_namespaced_pod(namespace=namespace, limit=max_pods,
                                               field_selector=field_selector).items
            cpureqs, memreqs = [], []
            for pod in pods:
                for container in pod.spec.containers:
                    if container.name != app_name:
                        res = container.resources
                        reqs = defaultdict(lambda: 0, res.requests or {})
                        cpureqs.append(Q_(reqs["cpu"]))
                        memreqs.append(Q_(reqs["memory"]))

            node_cpu_request_default_other += sum(cpureqs)
            node_memory_request_default_other += sum(memreqs)

            dict = {}
            dict['name'] = node_name
            dict['cpu'] = float(allocatabale_cpu - node_cpu_request_all + node_cpu_request_default - node_cpu_request_default_other) * 1000
            dict['memory'] = float(allocatable_memory - node_memory_request_all + node_memory_request_default - node_memory_request_default_other) / (
                        1024 * 1024)

            allocatable_resources_per_node.append(dict)
    except:
        print("Connection timeout after " + str(timeout) + " seconds on cluster " + cluster)

    return allocatable_resources_per_node

def compute_available_resources(cluster):

    total_allocatable_cpu = 0
    total_allocatable_memory = 0

    available_cpu = 0
    available_memory = 0

    total_cpu_request = 0
    total_memory_request = 0

    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    available_resources_per_node = []

    try:
        for node in core_v1.list_node(_request_timeout=timeout).items[1:]:
            stats          = {}
            node_name      = node.metadata.name
            allocatable    = node.status.allocatable
            allocatabale_cpu = Q_(allocatable['cpu']).to('m')
            allocatable_memory = Q_(allocatable['memory'])
            total_allocatable_cpu += allocatabale_cpu
            total_allocatable_memory += allocatable_memory
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
            dict['cpu'] = float(allocatabale_cpu - node_cpu_request) * 1000
            dict['memory'] = float(allocatable_memory - node_memory_request) / (1024 * 1024)

            available_resources_per_node.append(dict)

            total_cpu_request += Q_(node_cpu_request)
            total_memory_request += Q_(node_memory_request).to('Ki')
        available_cpu = total_allocatable_cpu - total_cpu_request
        available_memory = total_allocatable_memory - total_memory_request

        available_cpu = float(str(available_cpu)[:-2])
        available_memory = float(str(available_memory)[:-3])
    except:
        print("Connection timeout after " + str(timeout) + " seconds on cluster " + cluster)
    return available_cpu, available_memory, available_resources_per_node

def getPerNodeResources(cluster):

    perNodeCPU = 0
    perNodeMemory = 0

    client_cluster = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    try:
        nodes = client_cluster.list_node(_request_timeout=timeout)

        perNodeCPU = Q_(nodes.items[1].status.capacity['cpu']).to('m')
        perNodeMemory = Q_(nodes.items[1].status.capacity['memory']).to('Ki')

        perNodeCPU = float(str(perNodeCPU)[:-2])
        perNodeMemory = float(str(perNodeMemory)[:-3])
    except:
        print("Connection timeout after " + str(timeout) + " seconds to " + cluster)

    return perNodeCPU, perNodeMemory

def checkClusterPossibility(cluster, app_cpu_request, app_memory_request):
    cluster_per_node_cpu, cluster_per_node_memory = getPerNodeResources(cluster)

    if app_cpu_request >= cluster_per_node_cpu or app_memory_request*1024 >= cluster_per_node_memory:
        return False
    else:
        return True

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

def findNearestClusters(input_cluster, original_clusters):

    if isinstance(input_cluster, dict):
        input_cluster = input_cluster['name']

    # TO DO: Specify cluster 0
    config.load_kube_config()
    api = client.CoreV1Api()

    sorted_list = []

    try:
        nodes = api.list_node(pretty=True, _request_timeout=timeout)
        nodes = [node for node in nodes.items if
                 'node-role.kubernetes.io/master' in node.metadata.labels]
        # get all addresses of the master
        addresses = nodes[0].status.addresses

        master_ip = [i.address for i in addresses if i.type == "InternalIP"][0]

        all_clusters = get_all_federation_clusters()

        # Don't consider cloud cluster for latency comparison
        # TO DO: Install and configure serf on cloud cluster
        fog_only_clusters = []
        for cluster in all_clusters:
            if 'cloud' not in cluster:
                fog_only_clusters.append(cluster)
        candidate_clusters = list(filter(lambda x: x not in original_clusters, fog_only_clusters))

        rtt_dict = {}
        #sorted_list = []
        for c in candidate_clusters:
            command = 'serf rtt -rpc-addr=' + master_ip + ':7474 ' + c + ' ' + input_cluster
            result = subprocess.getoutput(command)
            # Need exception handling for the case when serf is not installed or available
            if 'Error' in result:
                print("There is error connecting to serf .........................................")
            result = float(result.split()[-2])
            rtt_dict[c] = result
        s = sorted(rtt_dict.items(), key=lambda x: x[1], reverse=False)
        for k, v in s:
            sorted_list.append(k)

        print("Sorted list of clusters ....", sorted_list)
    except:
        print("Connection timeout after " + str(timeout) + " seconds to host cluster")
    return sorted_list

def getFogAppLocationsByResource(clusters_qty):
    available_cpu = {}
    available_memory = {}
    all_clusters = get_all_federation_clusters()

    for cluster in all_clusters:
        available_cpu[cluster], available_memory[cluster] = compute_available_resources(cluster)

    sorted_dict = dict(sorted(available_memory.items(),
                              key=operator.itemgetter(1),
                              reverse=True))

    if clusters_qty > len(all_clusters):
        clusters_qty = len(all_clusters)

    fogapp_locations = []

    for key in sorted_dict:
        fogapp_locations.append(key)

    fogapp_locations = fogapp_locations[:clusters_qty]

    return fogapp_locations

def getControllerMasterIP():
    # TO DO: Specify cluster 0
    config.load_kube_config()
    #api = client.CoreV1Api(api_client=config.new_client_from_config(context="cluster0"))
    api = client.CoreV1Api()
    master_ip = ""
    try:
        nodes = api.list_node(pretty=True, _request_timeout=timeout)
        nodes = [node for node in nodes.items if
                 'node-role.kubernetes.io/master' in node.metadata.labels]
        # get all addresses of the master
        addresses = nodes[0].status.addresses

        master_ip = [i.address for i in addresses if i.type == "InternalIP"][0]
    except:
        print("Connection timeout after " + str(timeout) + " seconds to host cluster")

    return master_ip

def getFogAppLocations(app_name, app_namespace, app_cpu_request, app_memory_request, replicas, clusters_qty, placement_policy, mode):
    master_ip = getControllerMasterIP()
    prom_host = os.getenv("PROMETHEUS_DEMO_SERVICE_SERVICE_HOST", master_ip)
    prom_port = os.getenv("PROMETHEUS_DEMO_SERVICE_SERVICE_PORT", "30090")
    prom_url = "http://" + prom_host + ":" + prom_port

    # Creating the prometheus connect object with the required parameters
    pc = PrometheusConnect(url=prom_url, disable_ssl=True)

    # TO DO get all federation clusters except cloud
    all_clusters = get_all_federation_clusters()

    print("List of all clusters ................", all_clusters)

    fog_only_clusters = []
    for cluster in all_clusters:
        if 'cloud' not in cluster:
            fog_only_clusters.append(cluster)

    print("Fog - only clusters .....", fog_only_clusters)

    cluster_network_receive = {}

    possible_clusters = []
    for cluster in fog_only_clusters:
        if checkClusterPossibility(cluster, app_cpu_request, app_memory_request) == True:
            possible_clusters.append(cluster)
    print("List of possible clusters ..............", possible_clusters)

    eligible_clusters = []
    if len(possible_clusters) == 0:
        eligible_clusters = []
    else:
        for cluster in possible_clusters:
            # if checkClusterEligibility(cluster, app_cpu_request, app_memory_request, replicas) == True:
            #     eligible_clusters.append(cluster)
            # Get eligible clusters and their maximum capacity
            if mode == 'create':
                maximum_replicas = getMaximumReplicas(cluster, app_cpu_request, app_memory_request)
            elif mode == 'update':
                maximum_replicas = getAllocatableCapacity(cluster, app_cpu_request, app_memory_request, app_name, app_namespace)

            if maximum_replicas > 0:
                dict = {}

                dict['name'] = cluster
                dict['max_replicas'] = maximum_replicas
                eligible_clusters.append(dict)

    print("List of Eligible clusters ..............", eligible_clusters)

    if len(eligible_clusters) == 0:
        fogapp_locations = []
        all_clusters = get_all_federation_clusters()

        for cluster in all_clusters:
            if 'cloud' in cluster:
                dict = {}
                dict['name'] = cluster
                dict['max_replicas'] = replicas * clusters_qty
                fogapp_locations.append(dict)
        return fogapp_locations
    else:
        sorted_eligible_clusters = []
        if placement_policy == 'most_traffic' or placement_policy == 'most-traffic':
            for cluster in eligible_clusters:
                if mode == 'create':
                    query = "sum(instance:node_network_receive_bytes_excluding_lo:rate1m{cluster_name='" + cluster['name'] + "'})"
                elif mode == 'update':
                    query = "sum(irate(container_network_receive_bytes_total{cluster_name='" + cluster['name'] + "', namespace='" + app_namespace + "', pod=~'frontend.*'}[60s]))"

                # Here, we are fetching the values of a particular metric name
                result = pc.custom_query(query=query)

                #cluster_network_receive[cluster['name']] = float(result[0]['value'][1])
                if len(result) > 0:
                    cluster['ntk_rcv'] = float(result[0]['value'][1])
                else:
                    cluster['ntk_rcv'] = 0.0

            # sorted_dict = dict(sorted(cluster_network_receive.items(),
            #                           key=operator.itemgetter(1),
            #                           reverse=True))

            sorted_eligible_clusters = sorted(eligible_clusters, key = lambda i: i['ntk_rcv'], reverse=True)
        elif placement_policy == 'worst_fit' or placement_policy == 'worst-fit':
            sorted_eligible_clusters = sorted(eligible_clusters, key=lambda i: i['max_replicas'], reverse=True)
        elif placement_policy == 'best_fit' or placement_policy == 'best-fit':
            sorted_eligible_clusters = sorted(eligible_clusters, key=lambda i: i['max_replicas'])

        print("List of sorted traffic and policy ....", sorted_eligible_clusters)

        fogapp_locations = []

        for cluster in sorted_eligible_clusters:
            dict = {}
            dict['name'] = cluster['name']
            dict['max_replicas'] = cluster['max_replicas']
            fogapp_locations.append(dict)

        # for key in sorted_dict:
        #     fogapp_locations.append(key)

        all_clusters = get_all_federation_clusters()
        # if 'cloud' in all_clusters:
        #     fogapp_locations.append('cloud')
        for cluster in all_clusters:
            if 'cloud' in cluster:
                dict = {}
                dict['name'] = cluster
                dict['max_replicas'] = replicas
                fogapp_locations.append(dict)

        print("Final list of clusters which will host the app in the Default case ....", fogapp_locations)

        #fogapp_locations = fogapp_locations[:clusters_qty]
        return fogapp_locations

def getFogAppClusters(name, namespace):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    namespace = namespace
    plural = 'multiclusterdeployments'

    current_clusters = []
    original_clusters = []

    api_response = api.list_namespaced_custom_object(group=group, version=version, namespace=namespace, plural=plural)

    for item in api_response['items']:
        if item['metadata']['name'] == name:
            original_clusters = item['status']['create_fn']['fogapp_locations']
            if 'update_fn' in item['status']:
                current_clusters = item['status']['update_fn']['fogapp_locations']
            else:
                current_clusters = item['status']['create_fn']['fogapp_locations']

    return current_clusters, original_clusters

def getServiceClusters(name, namespace):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    namespace = namespace
    plural = 'multiclusterservices'

    current_clusters = []
    original_clusters = []

    api_response = api.list_namespaced_custom_object(group=group, version=version, namespace=namespace, plural=plural)

    for item in api_response['items']:
        if item['metadata']['name'] == name:
            if item['status'] != "":
                if 'create_fn' in item['status']:
                    original_clusters = item['status']['create_fn']['fogapp_locations']
                if 'update_fn' in item['status']:
                    current_clusters = item['status']['update_fn']['fogapp_locations']
                elif 'create_fn' in item['status']:
                    current_clusters = item['status']['create_fn']['fogapp_locations']

    return current_clusters, original_clusters


def getCloudCluster():
    all_clusters = get_all_federation_clusters()
    cloud_cluster = ''
    for cluster in all_clusters:
        if 'cloud' in cluster:
            cloud_cluster = cluster
    return cloud_cluster

def createDeployment(cluster, deployment_body, namespace):
    core_v1 = client.AppsV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.create_namespaced_deployment(namespace=namespace, body=deployment_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when creating Deployment on " + cluster )


def createService(cluster, service_body, namespace):
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.create_namespaced_service(namespace=namespace, body=service_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when creating Service on " + cluster)

def deleteDeployment(cluster, deployment_name, namespace):
    core_v1 = client.AppsV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.delete_namespaced_deployment(namespace=namespace, name=deployment_name, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when deleting Deployment from " + cluster)

def deleteService(cluster, service_name, namespace):
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.delete_namespaced_service(namespace=namespace, name=service_name, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when deleting Service from " + cluster)

def patchDeployment(cluster, deployment_name, deployment_body, namespace):
    core_v1 = client.AppsV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.patch_namespaced_deployment(namespace=namespace, name=deployment_name, body=deployment_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when patching Deployment on " + cluster)


def patchService(cluster, service_name, service_body, namespace):
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.patch_namespaced_service(namespace=namespace, name=service_name, body=service_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when patching Service on " + cluster)

def createJob(cluster, job_body, namespace):
    core_v1 = client.BatchV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.create_namespaced_job(namespace=namespace, body=job_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when creating Job on " + cluster)

def patchJob(cluster, fogapp_name, job_body, namespace):
    core_v1 = client.BatchV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.patch_namespaced_job(namespace=namespace, name=fogapp_name, body=job_body, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when patching Job on " + cluster)

def deleteJob(cluster, fogapp_name, namespace):
    core_v1 = client.BatchV1Api(api_client=config.new_client_from_config(context=cluster))
    try:
        core_v1.delete_namespaced_job(namespace=namespace, name=fogapp_name, _request_timeout=timeout)
    except:
        print("Connection timeout after " + str(timeout) + " seconds when deleting Job from " + cluster)