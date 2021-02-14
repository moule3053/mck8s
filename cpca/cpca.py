import kopf
import time
import math
import pandas as pd
from utils import getFlavor, provisionCloudCluster, get_all_federation_clusters, cloudClusterInfo, \
    scaleOut, getMachineDeployment, cloudNodesResources, patchMachineDeployment, getCloudApps, \
    deprovisionCloudCluster

# Create federated application -- initial placement
@kopf.daemon('fogguru.eu', 'v1', 'cloudprovisioners', initial_delay=15)
def create_fn(body, spec, stopped, **kwargs):
    name = body['metadata']['name']
    master_ip = spec['floatingIP']
    gateway_ip = spec['gatewayIP']
    ext_network_id = spec['extNetworkID']
    clouds_yaml = spec['cloudsYaml']
    cert_text = spec['certText']
    cloud_cluster_name = spec['cloudClusterName']
    influxdb_ip = spec['influxDBIP']
    securityGroupID = spec['securityGroupID']

    SCAN_INTERVAL = 30
    SCALE_IN_DELAY = 11 * 60
    SCALE_OUT_DELAY = 30
    SCALE_IN_THRESHOLD = 0.5
    DEPROVISION_DELAY = 21 * 60

    scale_in_delay = SCALE_IN_DELAY
    deprovision_delay = DEPROVISION_DELAY

    scale_in = False

    while not stopped:
        scale_in = False
        all_clusters = get_all_federation_clusters()
        print("List of all clusters ...", all_clusters)
        if cloud_cluster_name not in all_clusters:
            log_node_count = 0
            print("No cloud cluster found ....")
            k8s_flavor, node_count = getFlavor()
            print("Selected k8s flavor and number of nodes ................", k8s_flavor, node_count)
            if node_count !=0:
                print("We now need to provision a cloud cluster ...................")
                provisionCloudCluster(cloud_cluster_name, k8s_flavor, node_count, master_ip, gateway_ip, ext_network_id, clouds_yaml, cert_text, influxdb_ip, securityGroupID)
            print("Nothing to do ... sleeping for ......................", SCAN_INTERVAL, "secs ................")
            time.sleep(SCAN_INTERVAL)
        else:
            machinedeployment_name, machinedeployment_namespace, log_node_count = getMachineDeployment()
            print("Autoscaling cloud cluster ..............")

            scale_in = False

            all_clusters = get_all_federation_clusters()
            cloud_cluster = ""

            for cluster in all_clusters:
                if 'cloud' in cluster:
                    cloud_cluster = cluster

            pending_pods_count, total_cpu, total_memory = cloudClusterInfo(cloud_cluster)

            if pending_pods_count > 0:
                scale_in = False
                deprovision = False
                print("There are pending pods, wait for 30 seconds before scaling out .........................")
                time.sleep(SCALE_OUT_DELAY)
                print("Check again if there are still pending pods ............................................")
                pending_pods_count, total_cpu, total_memory = cloudClusterInfo(cloud_cluster)
                if pending_pods_count > 0:
                    scale_in = False
                    scale_in_delay = SCALE_IN_DELAY
                    deprovision = False
                    print("Scale out now ....................")

                    scaleOut(cloud_cluster, pending_pods_count, total_cpu, total_memory / (1024 * 1024))
                else:
                    scale_in = True
            else:
                scale_in = True

            if scale_in == True:

                machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas = getMachineDeployment()

                if machinedeployment_replicas <= 1:
                    deprovision = False
                    cloud_apps = getCloudApps()

                    if len(cloud_apps) == 0:
                        deprovision = True

                    if machinedeployment_replicas <= 1 and deprovision == False:
                        deprovision_delay = DEPROVISION_DELAY
                        print("De-provisioning delay delay in now reset to .....", DEPROVISION_DELAY)
                    elif machinedeployment_replicas <= 1 and deprovision == True:
                        deprovision_delay = deprovision_delay - 60
                        print("De-provisioning delay is now ..........", deprovision_delay)

                    if deprovision_delay == 0 and deprovision == True:
                        deprovisionCloudCluster(cloud_cluster, machinedeployment_namespace)
                        deprovision_delay = DEPROVISION_DELAY
                else:
                    resources_per_node = cloudNodesResources(machinedeployment_namespace)

                    worker_nodes_count = len(resources_per_node)
                    candidate_nodes_count = 0
                    desired_nodes_count = 0

                    for node in resources_per_node:
                        if node['total_cpu_request'] < SCALE_IN_THRESHOLD * node['available_cpu'] and \
                                node['total_memory_request'] < SCALE_IN_THRESHOLD * node['available_memory']:
                            candidate_nodes_count += 1

                    if candidate_nodes_count >= 2:
                        desired_nodes_count = worker_nodes_count - math.floor(candidate_nodes_count / 2)

                    if desired_nodes_count > 0:
                        scale_in = True
                    else:
                        scale_in = False

                    if desired_nodes_count > 0 and scale_in == False:
                        scale_in_delay = SCALE_IN_DELAY
                        print("Scale in delay in now reset to .....", SCALE_IN_DELAY)
                    elif desired_nodes_count > 0 and scale_in == True:
                        scale_in_delay = scale_in_delay - 60
                        print("Scale in delay is now ..........", scale_in_delay)

                    if scale_in_delay == 0 and scale_in == True:
                        scale_in_delay = SCALE_IN_DELAY
                        scale_in = False
                        print("Patching machine deployment to ........", desired_nodes_count)
                        patchMachineDeployment(cloud_cluster, machinedeployment_name, machinedeployment_namespace, machinedeployment_replicas, desired_nodes_count)

            time.sleep(SCAN_INTERVAL)
