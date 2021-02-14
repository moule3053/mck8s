from kubernetes import client, config
from collections import defaultdict
from pint        import UnitRegistry
import json

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
ureg.define('n = 1/1000000000 * kcpuunits')
ureg.define('m = 1/1000 * kcpuunits')
ureg.define('k = 1000 * kcpuunits')
ureg.define('M = k^2')
ureg.define('G = k^3')
ureg.define('T = k^4')
ureg.define('P = k^5')
ureg.define('E = k^6')

def getAveragePodUsage(cluster, name):
    avg_cpu_usage = 0
    avg_memory_usage = 0

    namespace = 'default'
    config.load_kube_config()

    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    try:
        ret_metrics = core_v1.api_client.call_api(
            '/apis/metrics.k8s.io/v1beta1/namespaces/' + namespace + '/pods', 'GET',
            auth_settings=['BearerToken'], response_type='json', _preload_content=False, _request_timeout=timeout)
        response = ret_metrics[0].data.decode('utf-8')
        response = json.loads(response)

        cpu_usage = 0
        memory_usage = 0
        for item in response['items']:
            if name in item['metadata']['name']:
                for container in item['containers']:
                    cpu_usage += Q_(container['usage']['cpu']).to('m')
                    memory_usage += Q_(container['usage']['memory']).to('Ki')
    except:
        print("Error getting metrics .............")
        cpu_usage = 0
        memory_usage = 0


    total_cpu_limit = 0
    total_mem_limit = 0

    try:
        pods = core_v1.list_namespaced_pod(namespace=namespace, _request_timeout=timeout)

        for item in pods.items:
            #if item.status.phase == 'Running':
            for container in item.spec.containers:
                if container.name == name:
                    #print("Dimensionality of cpu limit ....", Q_(container.resources.limits['cpu']).dimensionality)
                    total_cpu_limit += Q_(container.resources.limits['cpu']).to('m')
                    total_mem_limit += Q_(container.resources.limits['memory']).to('Ki')
        # Exception division by zero
        if total_cpu_limit !=0:
            avg_cpu_usage = cpu_usage / total_cpu_limit
            avg_cpu_usage = float(avg_cpu_usage * 100)

        if total_mem_limit != 0:
            avg_memory_usage = memory_usage / total_mem_limit
            avg_memory_usage = float(avg_memory_usage * 100)
    except:
        print("Error getting pods and their resource usage ..............")
        #pass
        avg_cpu_usage = 0
        avg_memory_usage = 0

    return avg_cpu_usage, avg_memory_usage

def getMultiClusterDeployment(name):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    namespace = 'default'
    plural = 'multiclusterdeployments'

    current_clusters = []
    original_clusters = []
    deployment_spec = {}

    try:
        api_response = api.list_namespaced_custom_object(group=group, version=version, namespace=namespace, plural=plural, _request_timeout=timeout)

        for item in api_response['items']:
            if item['metadata']['name'] == name:
                deployment_spec = item['spec']
                original_clusters = item['status']['create_fn']['fogapp_locations']
                if 'update_fn' in item['status']:
                    current_clusters = item['status']['update_fn']['fogapp_locations']
                else:
                    current_clusters = item['status']['create_fn']['fogapp_locations']
    except:
        print("Could not get response from api server .....")

    return original_clusters, current_clusters, deployment_spec

def getMultiClusterService(name):
    config.load_kube_config()
    api = client.CustomObjectsApi()

    group = 'fogguru.eu'
    version = 'v1'
    namespace = 'default'
    plural = 'multiclusterservices'

    current_clusters = []
    original_clusters = []
    service_spec = {}
    service_metadata = {}

    try:
        api_response = api.list_namespaced_custom_object(group=group, version=version, namespace=namespace, plural=plural, _request_timeout=timeout)

        for item in api_response['items']:
            if item['metadata']['name'] == name:
                service_metadata = item['metadata']
                service_spec = item['spec']
                #original_clusters = item['status']['create_fn']['fogapp_locations']
                if 'update_fn' in item['status']:
                    current_clusters = item['status']['update_fn']['fogapp_locations']
                else:
                    current_clusters = item['status']['create_fn']['fogapp_locations']
    except:
        print("Could not get response from api server .....")

    return current_clusters, service_spec, service_metadata

def getCurrentReplicas(cluster, name):
    namespace = 'default'
    config.load_kube_config()
    core_v1 = client.CoreV1Api(api_client=config.new_client_from_config(context=cluster))

    pods = core_v1.list_namespaced_pod(namespace=namespace)

    current_replicas = 0

    for item in pods.items:
        #if item.status.phase == 'Running':
        for container in item.spec.containers:
            if container.name == name:
                current_replicas += 1

    return current_replicas
