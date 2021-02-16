import math
import time
import kopf
import collections
import json
import pandas as pd
from kubernetes import client, config
from utils import getAveragePodUsage, getCurrentReplicas, getMultiClusterDeployment, getMultiClusterService

# Create federated HPA
@kopf.daemon('fogguru.eu', 'v1', 'multiclusterhorizontalpodautoscalers', initial_delay=10)
def create_fn(body, spec, stopped, **kwargs):
    # Logging
    request_log_colnames = ['timestamp', 'cluster1', 'cluster2', 'cluster3', 'cluster4', 'cluster5', 'cloud']
    request_log = pd.DataFrame(columns=request_log_colnames)
    log_file_name = 'multicluster_hpa_logs_6_14022021.csv'

    timeout = 60
    fogapp_namespace = 'default'
    desired_cpu_util = spec['metrics'][0]['resource']['target']['averageUtilization']
    fogapp_name = spec['scaleTargetRef']['name']

    if 'minReplicas' in spec:
        min_replicas = spec['minReplicas']
    else:
        min_replicas = 1

    if 'maxReplicas' in spec:
        max_replicas = spec['maxReplicas']
    else:
        max_replicas = 100

    if not desired_cpu_util:
        raise kopf.HandlerFatalError(f"You need to specify cpu utilization threshold. Got {desired_cpu_util}.")

    # Get the clusters where the target fogapp is running
    original_clusters, deployment_clusters, deployment_spec = getMultiClusterDeployment(fogapp_name)
    service_clusters, service_spec, service_metadata = getMultiClusterService(fogapp_name)

    print("Current clusters ............", deployment_clusters)

    replicas_dict = {}

    # Configuration parameters
    SCAN_INTERVAL = 30
    SCALE_DOWN_DELAY = 11 * 60
    # Delay before deciding to go back to original cluster
    SCALE_BACK_DELAY = 15 * 60

    scale_down_delay = {}
    scaled_down = {}

    # Inititalize scale-in delay for the fogapp in each cluster
    for cluster in deployment_clusters:
        scale_down_delay[cluster] = SCALE_DOWN_DELAY
        scaled_down[cluster] = False
        #scale_back_delay[cluster] = SCALE_BACK_DELAY

    while not stopped:

        original_clusters, deployment_clusters_new, deployment_spec = getMultiClusterDeployment(fogapp_name)
        if collections.Counter(deployment_clusters_new) != collections.Counter(deployment_clusters):
            raise kopf.TemporaryError("Clusters not the same, perhaps new clusters detected. Go to next cycle ...", delay=5)

        # Deployment template
        deployment_template = "{'apiVersion': 'fogguru.eu/v1', 'kind': 'MultiClusterDeployment', 'metadata': {'name': '" + fogapp_name + "'}, 'spec': "
        deployment_json = deployment_template + str(deployment_spec) + "}"
        deployment_text = deployment_json.replace("'", "\"")
        deployment_body = json.loads(deployment_text)

        # Service template
        if 'io.cilium/global-service' in service_metadata:
            service_template = "{'apiVersion': 'fogguru.eu/v1', 'kind': 'MultiClusterService', 'metadata': {'annotations':{'io.cilium/global-service':'true'}, 'name': '" + fogapp_name + "'}, 'spec': "
        else:
            service_template = "{'apiVersion': 'fogguru.eu/v1', 'kind': 'MultiClusterService', 'metadata': {'name': '" + fogapp_name + "'}, 'spec': "

        service_json = service_template + str(service_spec) + "}"
        service_text = service_json.replace("'", "\"")
        service_body = json.loads(service_text)

        clusters_list = []
        desired_replicas_list = []
        total_desired_replicas = 0

        current_replicas_dict = {}

        # Autoscaling per cluster
        for cluster in deployment_clusters:
            clusters_list.append(cluster)

            # Get current resource usage
            avg_cpu_usage, avg_mem_usage = getAveragePodUsage(cluster, fogapp_name)
            # Get current number of replicas
            current_replicas = getCurrentReplicas(cluster, fogapp_name)

            current_replicas_dict[cluster] = current_replicas
            # Estimate desire number of replicas

            if current_replicas == 0:
                #desired_replicas = min_replicas
                time.sleep(30)
                raise kopf.TemporaryError("Current replicas = 0, go to next cycle ...", delay=30)
            else:
                desired_replicas = math.ceil(current_replicas * (avg_cpu_usage / desired_cpu_util))

            print("Current replicas ...", cluster, current_replicas)
            print("Desired replicas ....", cluster, desired_replicas)

            if desired_replicas < current_replicas:
                scale_down = True
            else:
                scale_down = False

            print("Current scale_down delay ...", scale_down_delay)

            if desired_replicas < current_replicas and scale_down == False:
                scale_down_delay[cluster] = SCALE_DOWN_DELAY
                print("Scale down delay ...", cluster, SCALE_DOWN_DELAY)
            elif desired_replicas < current_replicas and scale_down == True:
                scale_down_delay[cluster] = scale_down_delay[cluster] - 2*SCAN_INTERVAL
                print("Scale down delay ...", cluster, scale_down_delay[cluster])

            if desired_replicas >= current_replicas or (desired_replicas < current_replicas and scale_down_delay[cluster] == 0):
                replicas_dict[cluster] = desired_replicas
                total_desired_replicas += desired_replicas
                scale_down_delay[cluster] = SCALE_DOWN_DELAY
            else:
                replicas_dict[cluster] = current_replicas
                total_desired_replicas += current_replicas

        if total_desired_replicas < min_replicas:
            total_desired_replicas = min_replicas
        elif total_desired_replicas >= max_replicas:
            total_desired_replicas = max_replicas

        # Added for logging support
        cloud_cluster = ''
        for cluster in clusters_list:
            if 'cloud' in cluster:
                cloud_cluster = cluster
            else:
                cloud_cluster = 'cloud'

        log_cluster_list = ['cluster1', 'cluster2', 'cluster3', 'cluster4', 'cluster5', cloud_cluster]
        current_replicas_dict_updated = {}
        desired_replicas_dict_updated = {}

        for cluster in log_cluster_list:
            if cluster in clusters_list:
                current_replicas_dict_updated[cluster] = current_replicas_dict[cluster]
                desired_replicas_dict_updated[cluster] = replicas_dict[cluster]
            else:
                current_replicas_dict_updated[cluster] = 0
                desired_replicas_dict_updated[cluster] = 0

        print("Total desired replicas ................", total_desired_replicas)

        replicas_dict_original = {}
        for cluster in original_clusters:
            replicas_dict_original[cluster] = int(total_desired_replicas / len(original_clusters))

        deployment_body['spec']['replicaOverrides'] = list(replicas_dict_original.values())
        deployment_body['spec']['locations'] = ','.join(original_clusters)
        # Service should be available on all clusters where deployment is running
        service_body['spec']['locations'] = ','.join(clusters_list)


        api = client.CustomObjectsApi()

        # Patch MCD
        try:
            api.patch_namespaced_custom_object(
                group="fogguru.eu",
                version="v1",
                namespace=fogapp_namespace,
                plural="multiclusterdeployments",
                name=fogapp_name,
                body=deployment_body,
                _request_timeout=timeout,
            )
            print("Multi Cluster Deployment " + fogapp_name + " updated by Multi Cluster HPA")
        except:
            #time.sleep(30)
            raise kopf.TemporaryError("Error occurred while patching Multi Cluster Deployment ...............", delay=30)
            # print("Error occurred while patching application ...............")
            # continue

        # Patch MCS
        try:
            api.patch_namespaced_custom_object(
                group="fogguru.eu",
                version="v1",
                namespace=fogapp_namespace,
                plural="multiclusterservices",
                name=fogapp_name,
                body=service_body,
                _request_timeout=timeout,
            )
            print("Multi Cluster Service " + fogapp_name + " updated by Multi Cluster HPA")
        except:
            #time.sleep(30)
            raise kopf.TemporaryError("Error occurred while patching Multi Cluster Service ...............", delay=30)

        time.sleep(SCAN_INTERVAL)

# Delete fog app autoscaler
@kopf.on.delete('fogguru.eu', 'v1', 'multiclusterhorizontalpodautoscalers')
def delete(body, **kwargs):
    msg = f"Multi Cluster HPA {body['metadata']['name']} has been deleted"
    return {'message': msg}
